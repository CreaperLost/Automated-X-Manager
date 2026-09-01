"""Project-folder organization and compact media-grid behavior."""

from __future__ import annotations

from pathlib import Path

from x_auto.store.models import MediaUpload
from x_auto.store.repos import Database
from x_auto.ui.tab_create import MEDIA_IMAGE_COLUMNS, _media_rows_for_folder
from x_auto.utils.media_library import (
    ensure_project_media_dirs,
    list_images,
    list_media,
    safe_project_folder_name,
)


def test_project_folders_are_created_and_keep_readable_names(tmp_path: Path):
    folders = ensure_project_media_dirs(
        tmp_path / "media_cache",
        ["Propr", "Dreamcash (code: 1i9f9u)", "Ondo"],
    )

    assert list(folders) == ["Propr", "Dreamcash (code: 1i9f9u)", "Ondo"]
    assert all(folder.is_dir() for folder in folders.values())
    assert folders["Propr"].name == "Propr"


def test_unsafe_path_characters_cannot_escape_media_cache():
    assert safe_project_folder_name(" Alpha/Beta: Bot ") == "Alpha-Beta- Bot"


def test_list_images_only_returns_supported_files_in_selected_folder(tmp_path: Path):
    folder = tmp_path / "Project"
    nested = folder / "Nested"
    nested.mkdir(parents=True)
    (folder / "b.PNG").write_bytes(b"png")
    (folder / "a.jpg").write_bytes(b"jpg")
    (folder / "c.avif").write_bytes(b"avif")
    (folder / "notes.txt").write_text("ignore", encoding="utf-8")
    (nested / "hidden.png").write_bytes(b"nested")

    assert [path.name for path in list_images(folder)] == ["a.jpg", "b.PNG", "c.avif"]


def test_folder_rows_merge_database_metadata_with_disk_files(tmp_path: Path):
    folder = tmp_path / "Project"
    folder.mkdir()
    cached = folder / "cached.png"
    local = folder / "local.png"
    cached.write_bytes(b"cached")
    local.write_bytes(b"local")

    db = Database(tmp_path / "state.db")
    db.register_media_upload(
        MediaUpload(
            local_path=str(cached.resolve()),
            filename=cached.name,
            x_media_id="media-123",
            mime="image/png",
            size=cached.stat().st_size,
        )
    )
    rows = _media_rows_for_folder(db, folder)
    db.close()

    assert [row.filename for row in rows] == ["cached.png", "local.png"]
    assert rows[0].x_media_id == "media-123"
    assert rows[1].x_media_id is None


def test_list_media_includes_project_videos(tmp_path: Path):
    folder = tmp_path / "Project"
    folder.mkdir()
    (folder / "demo.mp4").write_bytes(b"mp4")
    (folder / "launch.MOV").write_bytes(b"mov")
    (folder / "poster.png").write_bytes(b"png")
    (folder / "notes.txt").write_text("ignore", encoding="utf-8")

    assert [path.name for path in list_media(folder)] == [
        "demo.mp4", "launch.MOV", "poster.png",
    ]


def test_media_previews_use_six_columns_per_row():
    assert MEDIA_IMAGE_COLUMNS == 6
