"""Media upload for X posts.

Images use the v2 one-shot endpoint. Videos use X's asynchronous v2
initialize/append/finalize/status flow.

Limits (verified Aug 2026):
  - Up to 4 images per post
  - 5 MB per image
  - 512 MB per video
  - media_ids expire in 24 hours

Cached uploads: `upload_image_cached()` consults the `media_uploads`
table in the DB before hitting X. If the same file was uploaded within
the last 24h and X returned a `media_id`, we reuse it instead of
re-uploading. This is a latency / round-trip optimization, not a cost
one — uploads are free on X — but it makes the publish flow snappier
when reposting the same image back-to-back.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from ..store.models import MediaUpload
from ..store.repos import Database
from ..utils.files import (
    MAX_IMAGE_BYTES,
    is_video_path,
    mime_from_extension,
    validate_image,
    validate_video,
)
from .auth import TokenManager
from .client import API_BASE, USER_AGENT, AuthExpiredError, XApiError, XClient


class MediaUploadError(XApiError):
    """Raised when a media upload fails or the file is invalid."""


VIDEO_CHUNK_BYTES = 4 * 1024 * 1024
VIDEO_PROCESSING_TIMEOUT_SECONDS = 5 * 60


@dataclass(frozen=True)
class _PreparedImage:
    """An X-compatible image payload ready for multipart upload."""

    filename: str
    content: bytes
    mime: str


def _prepare_image_upload(file_path: Path) -> _PreparedImage:
    """Read an image and transcode AVIF to JPEG for X compatibility."""
    validation = validate_image(file_path)
    if not validation.ok:
        raise MediaUploadError(400, validation.reason, url="/2/media/upload")

    if validation.mime != "image/avif":
        return _PreparedImage(file_path.name, file_path.read_bytes(), validation.mime)

    try:
        with Image.open(file_path) as source:
            source.load()
            if "A" in source.getbands():
                rgba = source.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = source.convert("RGB")
            output = BytesIO()
            rgb.save(output, format="JPEG", quality=90, optimize=True)
    except (OSError, UnidentifiedImageError) as exc:
        raise MediaUploadError(
            400,
            f"could not decode AVIF image '{file_path.name}': {exc}",
            url="/2/media/upload",
        ) from exc

    content = output.getvalue()
    if len(content) > MAX_IMAGE_BYTES:
        raise MediaUploadError(
            400,
            f"converted image too large: {len(content):,} bytes "
            f"(max {MAX_IMAGE_BYTES:,})",
            url="/2/media/upload",
        )
    return _PreparedImage(f"{file_path.stem}.jpg", content, "image/jpeg")


def upload_image_sync(
    file_path: Path,
    *,
    token_manager: TokenManager,
) -> str:
    """Synchronous one-shot image upload. Returns the media_id string.

    Use this from non-async contexts such as scripts.
    The async app uses `XClient.upload_image` instead.
    """
    prepared = _prepare_image_upload(file_path)

    try:
        access_token = token_manager.access_token()
    except RuntimeError as exc:
        raise AuthExpiredError(401, str(exc), url="/2/media/upload") from exc

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT,
    }
    files = {"media": (prepared.filename, prepared.content, prepared.mime),
             "media_category": (None, "tweet_image")}
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{API_BASE}/media/upload",
            files=files,
            headers=headers,
        )
    if resp.status_code >= 400:
        raise MediaUploadError(resp.status_code, resp.text[:500], url="/2/media/upload")
    body = resp.json()
    media_id = body.get("data", {}).get("id")
    if not media_id:
        raise MediaUploadError(500, f"no media id in response: {body}", url="/2/media/upload")
    return str(media_id)


# Attach the sync method to XClient as `upload_image` so callers can
# use either the async or sync path without re-importing.
async def upload_image_async(self: XClient, file_path: Path) -> str:
    """Async one-shot image upload. Returns the media_id string."""
    prepared = _prepare_image_upload(file_path)

    try:
        access_token = self.tokens.access_token()
    except RuntimeError as exc:
        raise AuthExpiredError(401, str(exc), url="/2/media/upload") from exc

    files = {"media": (prepared.filename, prepared.content, prepared.mime),
             "media_category": (None, "tweet_image")}
    # httpx.AsyncClient.request accepts the same shape as Client.
    resp = await self._http.post(  # type: ignore[attr-defined]
        "/media/upload",
        files=files,
        headers={"Authorization": f"Bearer {access_token}",
                 "User-Agent": USER_AGENT},
    )
    if resp.status_code >= 400:
        raise MediaUploadError(resp.status_code, resp.text[:500], url="/2/media/upload")
    body = resp.json()
    media_id = body.get("data", {}).get("id")
    if not media_id:
        raise MediaUploadError(500, f"no media id in response: {body}", url="/2/media/upload")
    return str(media_id)


XClient.upload_image = upload_image_async  # type: ignore[attr-defined]


def assert_image_count(count: int) -> None:
    if count > 4:
        raise MediaUploadError(400, f"too many images: {count} (max 4)")


def assert_image_size(size: int) -> None:
    if size > MAX_IMAGE_BYTES:
        raise MediaUploadError(400, f"image too large: {size:,} bytes (max {MAX_IMAGE_BYTES:,})")


def _checked_response(response: httpx.Response, phase: str) -> dict:
    """Return an X media response body or raise a phase-specific error."""
    if response.status_code >= 400:
        raise MediaUploadError(
            response.status_code,
            f"video {phase} failed: {response.text[:500]}",
            url=str(response.request.url),
        )
    try:
        return response.json()
    except ValueError as exc:
        raise MediaUploadError(
            502,
            f"video {phase} returned invalid JSON",
            url=str(response.request.url),
        ) from exc


def upload_video_sync(
    file_path: Path,
    *,
    token_manager: TokenManager,
) -> str:
    """Upload one video with X's v2 chunked flow and await processing."""
    validation = validate_video(file_path)
    if not validation.ok:
        raise MediaUploadError(400, validation.reason, url="/2/media/upload/initialize")

    try:
        access_token = token_manager.access_token()
    except RuntimeError as exc:
        raise AuthExpiredError(
            401, str(exc), url="/2/media/upload/initialize"
        ) from exc

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT,
    }
    with httpx.Client(timeout=120) as client:
        initialized = _checked_response(
            client.post(
                f"{API_BASE}/media/upload/initialize",
                json={
                    "media_category": "tweet_video",
                    "media_type": validation.mime,
                    "total_bytes": validation.size,
                },
                headers=headers,
            ),
            "initialization",
        )
        media_id = str(initialized.get("data", {}).get("id") or "")
        if not media_id:
            raise MediaUploadError(
                502,
                f"video initialization returned no media id: {initialized}",
                url="/2/media/upload/initialize",
            )

        with file_path.open("rb") as source:
            segment_index = 0
            while chunk := source.read(VIDEO_CHUNK_BYTES):
                _checked_response(
                    client.post(
                        f"{API_BASE}/media/upload/{media_id}/append",
                        json={
                            "media": base64.b64encode(chunk).decode("ascii"),
                            "segment_index": segment_index,
                        },
                        headers=headers,
                    ),
                    f"segment {segment_index}",
                )
                segment_index += 1

        finalized = _checked_response(
            client.post(
                f"{API_BASE}/media/upload/{media_id}/finalize",
                headers=headers,
            ),
            "finalization",
        )
        processing = finalized.get("data", {}).get("processing_info")
        deadline = time.monotonic() + VIDEO_PROCESSING_TIMEOUT_SECONDS
        while processing:
            state = str(processing.get("state", "")).lower()
            if state == "succeeded":
                break
            if state == "failed":
                detail = processing.get("error") or processing
                raise MediaUploadError(
                    422,
                    f"X could not process video: {detail}",
                    url="/2/media/upload",
                )
            if time.monotonic() >= deadline:
                raise MediaUploadError(
                    408,
                    "timed out waiting for X to process the video",
                    url="/2/media/upload",
                )
            delay = min(max(int(processing.get("check_after_secs", 1)), 1), 10)
            time.sleep(delay)
            status = _checked_response(
                client.get(
                    f"{API_BASE}/media/upload",
                    params={"command": "STATUS", "media_id": media_id},
                    headers=headers,
                ),
                "status check",
            )
            processing = status.get("data", {}).get("processing_info")

    return media_id


def upload_image_cached(
    file_path: Path,
    *,
    token_manager: TokenManager,
    db: Database,
) -> str:
    """Upload (or re-use) an image and return X's media_id.

    Order of operations:
      1. Validate the file locally (size, MIME).
      2. Look up `media_uploads` by absolute path.
         - If a row exists with a non-null x_media_id that was set
           within `MEDIA_ID_TTL_SECONDS` (24h), return it as-is.
      3. Otherwise POST the file to /2/media/upload.
      4. Persist (or refresh) the row with the new x_media_id and a
         timestamp of "now".

    The function is synchronous (matches `upload_image_sync`); the
    async app uses `upload_image_async` in conjunction with
    `XClient` and a fresh coroutine for the network call.
    """
    validation = validate_image(file_path)
    if not validation.ok:
        raise MediaUploadError(400, validation.reason, url="/2/media/upload")

    abs_path = str(file_path.resolve())
    existing = db.get_media_upload_by_path(abs_path)
    if existing is not None and existing.is_still_valid and existing.x_media_id:
        return existing.x_media_id

    try:
        media_id = upload_image_sync(file_path, token_manager=token_manager)
    except (AuthExpiredError, MediaUploadError, httpx.HTTPError):
        # Propagate; the caller logs the failure. Don't pollute the cache
        # with a half-written row on failure.
        raise

    now = datetime.now()
    entry = MediaUpload(
        local_path=abs_path,
        filename=file_path.name,
        x_media_id=media_id,
        x_media_id_uploaded_at=now,
        mime=validation.mime,
        size=validation.size,
    )
    db.register_media_upload(entry)
    db.update_media_upload_x_id(abs_path, media_id, now)
    return media_id


def upload_media_cached(
    file_path: Path,
    *,
    token_manager: TokenManager,
    db: Database,
) -> str:
    """Upload or reuse an image/video from the project media library."""
    if not is_video_path(file_path):
        return upload_image_cached(file_path, token_manager=token_manager, db=db)

    validation = validate_video(file_path)
    if not validation.ok:
        raise MediaUploadError(400, validation.reason, url="/2/media/upload/initialize")

    abs_path = str(file_path.resolve())
    existing = db.get_media_upload_by_path(abs_path)
    if existing is not None and existing.is_still_valid and existing.x_media_id:
        return existing.x_media_id

    media_id = upload_video_sync(file_path, token_manager=token_manager)
    now = datetime.now()
    db.register_media_upload(MediaUpload(
        local_path=abs_path,
        filename=file_path.name,
        x_media_id=media_id,
        x_media_id_uploaded_at=now,
        mime=mime_from_extension(file_path.name),
        size=validation.size,
    ))
    db.update_media_upload_x_id(abs_path, media_id, now)
    return media_id
