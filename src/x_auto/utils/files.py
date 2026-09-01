"""File validation utilities: image size and MIME.

The local library accepts JPEG, PNG, GIF, WebP, and AVIF images up to
5 MB each. AVIF files are converted to JPEG by the publish pipeline
before they are sent to X.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_VIDEO_BYTES = 512 * 1024 * 1024  # 512 MB
MAX_IMAGES_PER_POST = 4

# Image MIME types accepted by the local library.
ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/avif", "image/jpeg", "image/png", "image/gif", "image/webp"}
)
ALLOWED_VIDEO_MIMES: frozenset[str] = frozenset(
    {"video/mp4", "video/quicktime", "video/webm"}
)

# Map file extension to MIME. Used when python-magic isn't available.
_EXT_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


@dataclass(frozen=True)
class ImageValidation:
    ok: bool
    reason: str = ""
    mime: str = ""
    size: int = 0


def mime_from_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return _EXT_TO_MIME.get(suffix, "")


def validate_image(path: Path) -> ImageValidation:
    """Validate a local image accepted by the app.

    Reads the extension and the file size. We don't sniff the bytes
    in v1 (would need python-magic) — the extension check is
    sufficient for trusted local files uploaded via Streamlit.
    """
    if not path.exists() or not path.is_file():
        return ImageValidation(ok=False, reason=f"file not found: {path}")
    size = path.stat().st_size
    if size == 0:
        return ImageValidation(ok=False, reason=f"file is empty: {path}", size=size)
    if size > MAX_IMAGE_BYTES:
        return ImageValidation(
            ok=False,
            reason=f"image too large: {size:,} bytes (max {MAX_IMAGE_BYTES:,})",
            size=size,
        )
    mime = mime_from_extension(path.name)
    if mime not in ALLOWED_IMAGE_MIMES:
        return ImageValidation(
            ok=False,
            reason=f"unsupported image type '{mime}'; allowed: {sorted(ALLOWED_IMAGE_MIMES)}",
            mime=mime,
            size=size,
        )
    return ImageValidation(ok=True, mime=mime, size=size)


def validate_video(path: Path) -> ImageValidation:
    """Validate a local video for X's chunked media upload flow."""
    if not path.exists() or not path.is_file():
        return ImageValidation(ok=False, reason=f"file not found: {path}")
    size = path.stat().st_size
    if size == 0:
        return ImageValidation(ok=False, reason=f"file is empty: {path}", size=size)
    if size > MAX_VIDEO_BYTES:
        return ImageValidation(
            ok=False,
            reason=f"video too large: {size:,} bytes (max {MAX_VIDEO_BYTES:,})",
            size=size,
        )
    mime = mime_from_extension(path.name)
    if mime not in ALLOWED_VIDEO_MIMES:
        return ImageValidation(
            ok=False,
            reason=f"unsupported video type '{mime}'; allowed: {sorted(ALLOWED_VIDEO_MIMES)}",
            mime=mime,
            size=size,
        )
    return ImageValidation(ok=True, mime=mime, size=size)


def is_video_path(path: str | Path) -> bool:
    """Return whether a path has a supported video extension."""
    return mime_from_extension(Path(path).name) in ALLOWED_VIDEO_MIMES


def safe_filename(name: str) -> str:
    """Return a path-traversal-safe version of a filename."""
    # Treat both POSIX and Windows separators as path separators on every OS.
    name = Path(name.replace("\\", "/")).name
    return name.replace("..", "_").replace("/", "_")
