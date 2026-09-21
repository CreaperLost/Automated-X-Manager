"""Tests for the centralized media catalog manager."""
from __future__ import annotations

from pathlib import Path

import pytest

from x_auto.utils.media_catalog import (
    get_media_description,
    list_project_media_with_descriptions,
    load_catalog,
    register_media,
    save_catalog,
)


def test_load_missing_catalog_returns_empty(tmp_path: Path):
    missing = tmp_path / "missing.json"
    assert load_catalog(missing) == {}


def test_save_and_load_catalog(tmp_path: Path):
    catalog_path = tmp_path / "media_catalog.json"
    data = {
        "Hyperliquid": {
            "chart.png": {
                "description": "Perp volume chart showing record ATH",
                "added_at": "2026-09-07T12:00:00",
            }
        }
    }
    save_catalog(catalog_path, data)
    loaded = load_catalog(catalog_path)
    assert loaded == data


def test_register_media_requires_description(tmp_path: Path):
    catalog_path = tmp_path / "media_catalog.json"
    with pytest.raises(ValueError, match="Media description is required"):
        register_media(catalog_path, "Hyperliquid", "banner.png", "   ")


def test_register_media_success(tmp_path: Path):
    catalog_path = tmp_path / "media_catalog.json"
    entry = register_media(
        catalog_path,
        project_name="TXFlow",
        filename="spot.mp4",
        description="Demo of spot trading with zero slippage",
    )
    assert entry["description"] == "Demo of spot trading with zero slippage"
    assert "added_at" in entry

    desc = get_media_description(catalog_path, "TXFlow", "spot.mp4")
    assert desc == "Demo of spot trading with zero slippage"


def test_list_project_media_with_descriptions(tmp_path: Path):
    media_cache = tmp_path / "media_cache"
    proj_dir = media_cache / "TXFlow"
    proj_dir.mkdir(parents=True)
    (proj_dir / "vid.mp4").write_bytes(b"fake mp4 content")
    (proj_dir / "img.png").write_bytes(b"fake png content")

    catalog_path = tmp_path / "media_catalog.json"
    register_media(
        catalog_path,
        "TXFlow",
        "vid.mp4",
        "Spot trading clip",
    )

    items = list_project_media_with_descriptions(media_cache, catalog_path, "TXFlow")
    assert len(items) == 2

    by_name = {item["filename"]: item for item in items}
    assert by_name["vid.mp4"]["is_video"] is True
    assert by_name["vid.mp4"]["description"] == "Spot trading clip"

    assert by_name["img.png"]["is_video"] is False
    assert by_name["img.png"]["description"] == ""
