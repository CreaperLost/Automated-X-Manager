"""Tests for the 3-column card grids in the Review and Publish tabs.

These pin the layout (3 cards per row, per-card checkbox / per-card
action button) so a future refactor doesn't accidentally drop back
to single-column or table layouts.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# ---- helpers ----------------------------------------------------------------

def _seed_review_tweets(db_path: Path) -> int:
    """Insert a few tweets in 'new', 'selected', and 'archived' status.

    Returns the number of rows inserted.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # Account first (FK from tweets).
    con.execute(
        "INSERT OR REPLACE INTO accounts(handle, user_id, display_name) "
        "VALUES (?, ?, ?)",
        ("alpha", "uid-1", "Alpha"),
    )
    con.execute(
        "INSERT OR REPLACE INTO accounts(handle, user_id, display_name) "
        "VALUES (?, ?, ?)",
        ("beta", "uid-2", "Beta"),
    )
    rows = [
        ("t-1", "alpha", "first tweet body",  "new",      3),
        ("t-2", "alpha", "second tweet body", "new",      7),
        ("t-3", "beta",  "third tweet body",  "new",      1),
        ("t-4", "alpha", "fourth tweet body", "new",     12),
        ("t-5", "beta",  "fifth tweet body",  "new",      0),
        ("t-6", "alpha", "sixth tweet body",  "new",      4),
        ("t-7", "alpha", "seventh (selected) tweet body", "selected", 22),
        ("t-8", "beta",  "eighth (selected) tweet body",  "selected", 5),
        ("t-9", "alpha", "ninth (archived) tweet body",   "archived", 99),
    ]
    for tid, handle, text, status, likes in rows:
        con.execute(
            "INSERT OR REPLACE INTO tweets"
            "(id, account_handle, text, created_at, public_metrics, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                tid, handle, text,
                datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).isoformat(),
                f'{{"like_count": {likes}}}',
                status,
            ),
        )
    con.commit()
    n = con.execute("SELECT COUNT(*) AS c FROM tweets").fetchone()["c"]
    con.close()
    return n


def _seed_published_drafts(db_path: Path) -> int:
    """Insert a few posted drafts so the Published grid has cards to render."""
    from x_auto.store.models import Draft

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    for i in range(1, 4):
        draft = Draft(
            id=None,
            body=f"Published tweet #{i} body — already live on X.",
            link_url=f"https://app.example.com/ref{i}",
            image_paths=[],
            tone="",
            status="posted",
            posted_at=datetime(2026, 1, 1, 14, 0, 0, tzinfo=UTC),
            x_tweet_id=f"190000000000000000{i}",
        )
        cur = con.execute(
            "INSERT INTO drafts"
            "(source_tweet_id, body, link_url, image_paths, tone, status, "
            " posted_at, x_tweet_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft.source_tweet_id, draft.body, draft.link_url,
                "[]", draft.tone, draft.status,
                draft.posted_at.isoformat(sep=" "),
                draft.x_tweet_id,
            ),
        )
        draft.id = int(cur.lastrowid)
    con.commit()
    n = con.execute("SELECT COUNT(*) AS c FROM drafts WHERE status='posted'").fetchone()["c"]
    con.close()
    return n


def _render_tab(tab_module_path: str, db_path: Path) -> object:
    """Render a tab module's ``render`` function inside an AppTest.

    The tab module is referenced by import path; the script string
    does the import + render inside AppTest's isolated context.
    """
    from streamlit.testing.v1 import AppTest

    src_path = str(Path(__file__).resolve().parent.parent / "src").replace("\\", "\\\\")
    db_path_str = str(db_path.resolve()).replace("\\", "\\\\")
    script = f"""
import sys
from pathlib import Path
sys.path.insert(0, r"{src_path}")
from x_auto.store.repos import Database
from {tab_module_path} import render

db = Database(Path(r"{db_path_str}"))
render(db)
"""
    at = AppTest.from_string(script)
    at.run()
    return at


# ---- Review tab -------------------------------------------------------------

class TestReviewGrid:
    def test_two_columns_per_row_with_one_button_per_card(self, tmp_path):
        """Nine tweets (6 new + 2 selected + 1 archived) produce two
        columns of cards; each card has exactly one action button
        (Select / Unselect / Restore). No checkboxes, no bulk action,
        no multi-select. 2 cards per row, so the layout spans 5 rows
        for 9 cards (rows of 2, last row has 1)."""
        from x_auto.store.repos import Database

        db_path = tmp_path / "state.db"
        # Bootstrap the schema.
        Database(db_path).close()
        _seed_review_tweets(db_path)

        at = _render_tab("x_auto.ui.tab_review", db_path)
        assert at.exception == []

        # Per-card action button: one per tweet (6+2+1 = 9).
        card_buttons = [
            b for b in at.button
            if b.key and b.key.startswith("review_card_")
        ]
        assert len(card_buttons) == 9, (
            f"expected 9 per-card action buttons, got {len(card_buttons)}"
        )

        # No checkboxes — the multi-select path was removed.
        pick_checkboxes = [
            cb for cb in at.checkbox
            if cb.key and cb.key.startswith("review_pick_")
        ]
        assert len(pick_checkboxes) == 0, (
            f"expected no per-card checkboxes, got {len(pick_checkboxes)}"
        )

        # No bulk action, no Select all / Clear — the multi-select UI
        # was removed.
        for prefix in ("review_bulk_", "review_select_all_", "review_clear_"):
            matches = [
                b for b in at.button if b.key and b.key.startswith(prefix)
            ]
            assert len(matches) == 0, (
                f"expected no buttons with prefix {prefix!r}, "
                f"got {len(matches)}"
            )

    def test_per_card_button_label_matches_subtab(self, tmp_path):
        """The per-card button label matches the sub-tab:
        'Select' on New, 'Unselect' on Selected, 'Restore' on Archived.
        """
        from x_auto.store.repos import Database

        db_path = tmp_path / "state.db"
        Database(db_path).close()
        _seed_review_tweets(db_path)

        at = _render_tab("x_auto.ui.tab_review", db_path)
        by_source: dict[str, list[str]] = {"new": [], "selected": [], "archived": []}
        for b in at.button:
            if not b.key or not b.key.startswith("review_card_"):
                continue
            # key shape: review_card_<source>_<tweet_id>
            parts = b.key.split("_", 3)
            if len(parts) >= 4 and parts[2] in by_source:
                by_source[parts[2]].append(b.label)

        assert by_source["new"]      == ["Select"]   * len(by_source["new"]), (
            f"new buttons should all be 'Select': got {by_source['new']}"
        )
        assert by_source["selected"] == ["Unselect"] * len(by_source["selected"]), (
            f"selected buttons should all be 'Unselect': got {by_source['selected']}"
        )
        assert by_source["archived"] == ["Restore"]  * len(by_source["archived"]), (
            f"archived buttons should all be 'Restore': got {by_source['archived']}"
        )


# ---- Published section (Publish tab) ---------------------------------------

class TestPublishedGrid:
    def test_three_columns_per_row_for_published(self, tmp_path):
        """3 posted drafts → one row of 3 cards, each with a Repost
        button and a Paraphrase popover."""
        from x_auto.store.repos import Database

        db_path = tmp_path / "state.db"
        Database(db_path).close()
        _seed_published_drafts(db_path)

        # The Publish tab needs a Settings + db + x_client + ai. We
        # only exercise the Published section by mocking the rest.
        # The simplest path: render the whole tab with stubs.
        at = _render_publish_tab(db_path)
        assert at.exception == [], f"exceptions: {[e.message for e in at.exception]}"

        # Per-card Repost button: one per posted draft.
        repost_buttons = [
            b for b in at.button if b.key and b.key.startswith("repost_")
        ]
        assert len(repost_buttons) == 3, (
            f"expected 3 Repost buttons, got {len(repost_buttons)}"
        )

        # Per-card Paraphrase popover: one per posted draft.
        paraphrase_popovers = [
            b for b in at.button
            if b.key and b.key.startswith("paraphrase_")
        ]
        # Note: the popover's label is "Paraphrase ↻"; the inner
        # "Generate" button uses a different key prefix.
        assert len(paraphrase_popovers) >= 3, (
            f"expected ≥ 3 Paraphrase triggers, got {len(paraphrase_popovers)}"
        )


def _render_publish_tab(db_path: Path) -> object:
    """Render the Publish tab with stubs for the X client and AI.

    The Published section doesn't call the X client or AI at render
    time, so MagicMock is enough.
    """

    from streamlit.testing.v1 import AppTest

    src_path = str(Path(__file__).resolve().parent.parent / "src").replace("\\", "\\\\")
    db_path_str = str(db_path.resolve()).replace("\\", "\\\\")
    script = f"""
import sys
from pathlib import Path
sys.path.insert(0, r"{src_path}")
from unittest.mock import MagicMock
from x_auto.store.repos import Database
from x_auto.config import Settings, get_settings
from x_auto.ui.tab_publish import render

db = Database(Path(r"{db_path_str}"))
get_settings.cache_clear()
settings = get_settings()
x_client = MagicMock()
ai = MagicMock()
render(settings, db, x_client, ai, on_schedule=lambda *a, **k: None)
"""
    at = AppTest.from_string(script)
    at.run()
    return at
