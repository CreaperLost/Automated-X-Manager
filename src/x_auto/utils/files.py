"""File validation utilities: image size, MIME, content sniff.

X accepts JPEG, PNG, GIF, WebP for images, up to 5 MB per image, up
to 4 images per post. The Create tab validates user uploads against
this before letting them into a draft, and the publish flow
re-validates before upload (defense in depth).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGES_PER_POST = 4

# X-allowed image MIME types.
ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

# Map file extension to MIME. Used when python-magic isn't available.
_EXT_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
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
    """Validate a local file as a postable X image.

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


def safe_filename(name: str) -> str:
    """Return a path-traversal-safe version of a filename."""
    name = Path(name).name  # strip any directory parts
    return name.replace("..", "_").replace("/", "_").replace("\\", "_")
