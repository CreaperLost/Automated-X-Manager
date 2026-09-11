"""Tests for semantic media auto-matching."""
from __future__ import annotations

from x_auto.utils.media_matching import match_best_media


def test_match_best_media_by_topic() -> None:
    project_media = [
        {
            "filename": "n1_mobile.mp4",
            "path": "/path/to/n1_mobile.mp4",
            "description": "N1 mobile trading app interface overview",
        },
        {
            "filename": "txflow_comp_august.mp4",
            "path": "/path/to/txflow_comp_august.mp4",
            "description": "TXFlow trading competition leaderboard and rewards clip",
        },
        {
            "filename": "txflow_spot.mp4",
            "path": "/path/to/txflow_spot.mp4",
            "description": "TXFlow spot trading engine demonstration with zero slippage",
        },
    ]

    # Matching trading competition
    match = match_best_media(
        project_media,
        text="Massive incentives announced for all participants today!",
        topic="trading competition leaderboard",
    )
    assert match is not None
    assert match["filename"] == "txflow_comp_august.mp4"
    assert match["match_score"] > 0


def test_match_best_media_by_source_text() -> None:
    project_media = [
        {
            "filename": "n1_mobile.mp4",
            "path": "/path/to/n1_mobile.mp4",
            "description": "N1 mobile trading app interface overview",
        },
        {
            "filename": "spot_engine.mp4",
            "path": "/path/to/spot_engine.mp4",
            "description": "Spot trading engine demonstration with zero slippage",
        },
    ]

    match = match_best_media(
        project_media,
        text="The new mobile interface is genuinely slick and lightning fast.",
        topic="",
    )
    assert match is not None
    assert match["filename"] == "n1_mobile.mp4"


def test_match_best_media_empty_or_no_match() -> None:
    assert match_best_media([], "Some text", "Some topic") is None

    project_media = [
        {
            "filename": "asset1.png",
            "path": "/path/to/asset1.png",
            "description": "Galactic nebula telescope observation",
        }
    ]
    # No overlapping tokens
    match = match_best_media(project_media, "crypto bitcoin blockchain", "defi tokens")
    assert match is None
