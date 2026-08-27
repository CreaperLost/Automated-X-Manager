"""Project:Link loader backed by `data/projects.csv`.

CSV format (header row required, two columns):
    name,url

    name - required, unique
    url  - required, must be a valid http(s) URL

Empty lines and rows starting with `#` are ignored. The first
non-comment line is treated as the header. Malformed rows are
skipped with a warning emitted to stderr.

The DB schema still has `description` and `tags` columns; the loader
just always sets them to empty. The Create tab reads only the URL
(via the project list) — descriptions and tags are not used in v2.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import Settings
from ..store.repos import Database

_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def csv_path(settings: Settings) -> Path:
    """Default location for the project CSV."""
    return settings.data_dir / "projects.csv"


def _validate_url(value: str) -> bool:
    if not value or not _URL_RE.match(value):
        return False
    try:
        parsed = urlparse(value)
        return bool(parsed.scheme) and bool(parsed.netloc)
    except ValueError:
        return False


def load_csv(path: Path) -> list[dict[str, Any]]:
    """Read a CSV file and return validated project dicts.

    Returns a list of:
        {"name": str, "url": str, "description": "", "tags": []}

    Empty/missing file -> []. Malformed rows are dropped, not raised.
    """
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            name = (row.get("name") or "").strip()
            url = (row.get("url") or "").strip()
            if not name or not url:
                print(
                    f"[projects] line {line_no}: missing name or url, skipping",
                    flush=True,
                )
                continue
            if not _validate_url(url):
                print(
                    f"[projects] line {line_no}: invalid url {url!r}, skipping",
                    flush=True,
                )
                continue
            if name in seen:
                print(
                    f"[projects] line {line_no}: duplicate name {name!r}, skipping",
                    flush=True,
                )
                continue
            seen.add(name)
            out.append(
                {
                    "name": name,
                    "url": url,
                    "description": "",
                    "tags": [],
                }
            )
    return out


def write_csv(path: Path, projects: list[dict[str, Any]]) -> None:
    """Write projects to a CSV file, overwriting the previous contents.

    Two-column output: name,url. description and tags fields on the
    input dicts are ignored (kept for compatibility with the DB
    schema; they just don't appear in the file).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["name", "url"])
        for p in projects:
            writer.writerow(
                [
                    (p.get("name") or "").strip(),
                    (p.get("url") or "").strip(),
                ]
            )


def sync_projects(settings: Settings, db: Database) -> int:
    """Replace the `projects` table with the contents of the CSV file.

    Returns the count written. Safe to call at app startup; idempotent.
    """
    projects = load_csv(csv_path(settings))
    db.replace_projects(projects)
    return len(projects)


def list_projects(db: Database) -> list[dict[str, Any]]:
    """Return all projects as plain dicts (Pydantic HttpUrl is awkward in the UI)."""
    return [
        {
            "name": p.name,
            "url": str(p.url),
            "description": p.description,
            "tags": p.tags,
        }
        for p in db.list_projects()
    ]
