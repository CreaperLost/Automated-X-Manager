"""Media upload for X posts.

We use the v2 one-shot endpoint `POST /2/media/upload` with multipart
for images. The chunked INIT/APPEND/FINALIZE flow is for video, which
v1 does not support.

Limits (verified Aug 2026):
  - Up to 4 images per post
  - 5 MB per image
  - 512 MB per video (out of scope v1)
  - media_ids expire in 24 hours

Cached uploads: `upload_image_cached()` consults the `media_uploads`
table in the DB before hitting X. If the same file was uploaded within
the last 24h and X returned a `media_id`, we reuse it instead of
re-uploading. This is a latency / round-trip optimization, not a cost
one — uploads are free on X — but it makes the publish flow snappier
when reposting the same image back-to-back.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx

from ..store.models import MediaUpload
from ..store.repos import Database
from ..utils.files import MAX_IMAGE_BYTES, validate_image
from .auth import TokenManager
from .client import API_BASE, USER_AGENT, AuthExpiredError, XApiError, XClient


class MediaUploadError(XApiError):
    """Raised when an image upload fails or the file is invalid."""


def upload_image_sync(
    file_path: Path,
    *,
    token_manager: TokenManager,
) -> str:
    """Synchronous one-shot image upload. Returns the media_id string.

    Use this from non-async contexts (e.g. APScheduler jobs, scripts).
    The async app uses `XClient.upload_image` instead.
    """
    validation = validate_image(file_path)
    if not validation.ok:
        raise MediaUploadError(400, validation.reason, url="/2/media/upload")

    try:
        access_token = token_manager.access_token()
    except RuntimeError as exc:
        raise AuthExpiredError(401, str(exc), url="/2/media/upload") from exc

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT,
    }
    with file_path.open("rb") as fh:
        files = {"media": (file_path.name, fh, validation.mime),
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
    validation = validate_image(file_path)
    if not validation.ok:
        raise MediaUploadError(400, validation.reason, url="/2/media/upload")

    try:
        access_token = self.tokens.access_token()
    except RuntimeError as exc:
        raise AuthExpiredError(401, str(exc), url="/2/media/upload") from exc

    with file_path.open("rb") as fh:
        files = {"media": (file_path.name, fh, validation.mime),
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
