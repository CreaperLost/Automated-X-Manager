"""Project-folder organization for the local personal image library."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

SUPPORTED_IMAGE_SUFFIXES = frozenset(
    {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
SUPPORTED_VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".webm"})
SUPPORTED_MEDIA_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | SUPPORTED_VIDEO_SUFFIXES
_UNSAFE_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_project_folder_name(project_name: str) -> str:
    """Return a readable, cross-platform folder name for a project."""
    cleaned = _UNSAFE_FOLDER_CHARS.sub("-", project_name.strip()).strip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Unnamed project"


def project_media_dir(cache_dir: Path, project_name: str) -> Path:
    """Return the media directory for one project without creating it."""
    return cache_dir / safe_project_folder_name(project_name)


def ensure_project_media_dirs(
    cache_dir: Path,
    project_names: Iterable[str],
) -> dict[str, Path]:
    """Create and return the folder mapped to every non-empty project name."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    folders: dict[str, Path] = {}
    for raw_name in project_names:
        name = str(raw_name or "").strip()
        if not name or name in folders:
            continue
        folder = project_media_dir(cache_dir, name)
        folder.mkdir(parents=True, exist_ok=True)
        folders[name] = folder
    return folders


def list_images(folder: Path) -> list[Path]:
    """List supported image files directly inside a media folder."""
    if not folder.is_dir():
        return []
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        ),
        key=lambda path: path.name.casefold(),
    )


def list_media(folder: Path) -> list[Path]:
    """List supported image and video files directly inside a media folder."""
    if not folder.is_dir():
        return []
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_MEDIA_SUFFIXES
        ),
        key=lambda path: path.name.casefold(),
    )
