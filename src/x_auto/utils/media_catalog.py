"""Centralized media catalog manager for project assets.

Tracks descriptions and metadata for media files organized under
`data/<niche>/media_cache/<project>/`. Stored in `data/<niche>/media_catalog.json`.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .files import is_video_path
from .media_library import list_media, project_media_dir


def catalog_path_for_data_dir(data_dir: Path) -> Path:
    """Return the centralized media_catalog.json path for a niche data dir."""
    return data_dir / "media_catalog.json"


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    """Load the JSON media catalog. Return empty dict if file does not exist."""
    if not path.is_file():
        return {}
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_catalog(path: Path, data: dict[str, dict[str, Any]]) -> None:
    """Save the JSON media catalog atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def register_media(
    catalog_path: Path,
    project_name: str,
    filename: str,
    description: str,
) -> dict[str, Any]:
    """Register or update an asset's description in the centralized catalog.

    Raises ValueError if description is empty or whitespace.
    """
    clean_desc = (description or "").strip()
    if not clean_desc:
        raise ValueError("Media description is required.")

    clean_proj = project_name.strip()
    if not clean_proj:
        raise ValueError("Project name is required.")

    catalog = load_catalog(catalog_path)
    entry = {
        "description": clean_desc,
        "added_at": datetime.now().isoformat(),
    }
    if clean_proj not in catalog:
        catalog[clean_proj] = {}

    catalog[clean_proj][filename] = entry
    save_catalog(catalog_path, catalog)
    return entry


def get_media_description(
    catalog_path: Path,
    project_name: str,
    filename: str,
) -> str:
    """Return the logged description of an asset, or empty string."""
    catalog = load_catalog(catalog_path)
    return (
        catalog.get(project_name.strip(), {})
        .get(filename, {})
        .get("description", "")
    )


def list_project_media_with_descriptions(
    media_cache_dir: Path,
    catalog_path: Path,
    project_name: str,
) -> list[dict[str, Any]]:
    """List all supported media files in a project's folder with their descriptions."""
    folder = project_media_dir(media_cache_dir, project_name)
    files = list_media(folder)
    catalog = load_catalog(catalog_path)
    proj_entries = catalog.get(project_name.strip(), {})

    results: list[dict[str, Any]] = []
    for f in files:
        entry = proj_entries.get(f.name, {})
        results.append({
            "filename": f.name,
            "path": str(f),
            "description": entry.get("description", ""),
            "added_at": entry.get("added_at", ""),
            "is_video": is_video_path(f),
        })
    return results
