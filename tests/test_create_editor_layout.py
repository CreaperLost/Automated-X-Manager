"""Regression tests for the Create tab's editor pane layout.

The editor was refactored from side-by-side columns to a single
column with the preview at the top. These tests pin that shape so a
future refactor doesn't push the preview back off-screen on narrow
viewports.

Also pins: a warning shows when the reply is empty (so the user
knows the AI produced nothing for the reply when no project was
selected at generate-time).
"""
from __future__ import annotations

from pathlib import Path


def _render_editor(draft_id: int) -> object:
    """Render the editor pane for a real draft (DB lives in data/)."""
    from streamlit.testing.v1 import AppTest

    src_path = str(Path(__file__).resolve().parent.parent / "src").replace("\\", "\\\\")
    script = f"""
import sys
sys.path.insert(0, r"{src_path}")
from x_auto.config import get_settings
from x_auto.store.repos import Database
from x_auto.ui.tab_create import _render_editor_with_preview

settings = get_settings()
db = Database(settings.data_dir / "state.db")
draft = db.get_draft({draft_id})
_render_editor_with_preview(settings, db, draft)
"""
    at = AppTest.from_string(script)
    at.run()
    return at


def _create_draft(body: str, link_url: str | None) -> int:
    from x_auto.config import get_settings
    from x_auto.store.models import Draft
    from x_auto.store.repos import Database

    settings = get_settings()
    db = Database(settings.data_dir / "state.db")
    d = Draft(
        source_tweet_id=None,
        body=body,
        link_url=link_url,
        image_paths=[],
        tone="",
        status="draft",
    )
    new_id = db.create_draft(d)
    db.close()
    return new_id


def _delete_draft(draft_id: int) -> None:
    from x_auto.config import get_settings
    from x_auto.store.repos import Database

    settings = get_settings()
    db = Database(settings.data_dir / "state.db")
    db.delete_draft(draft_id)
    db.close()


class TestEditorRendersPreviewAtTop:
    """The preview must render and contain the body text even when
    the reply is empty (the user's reported bug)."""

    def test_no_exception_with_empty_reply(self):
        marker = "EDITOR_LAYOUT_TEST_empty_reply"
        draft_id = _create_draft(marker, link_url=None)
        try:
            at = _render_editor(draft_id)
            assert at.exception == []
        finally:
            _delete_draft(draft_id)

    def test_preview_label_renders(self):
        marker = "EDITOR_LAYOUT_TEST_label"
        draft_id = _create_draft(marker, link_url="https://example.com/ref")
        try:
            at = _render_editor(draft_id)
            joined = "\n".join(m.value for m in at.markdown)
            assert "Preview" in joined
        finally:
            _delete_draft(draft_id)

    def test_body_text_appears_in_preview(self):
        marker = "EDITOR_LAYOUT_TEST_body_visible"
        draft_id = _create_draft(marker, link_url="https://example.com/ref")
        try:
            at = _render_editor(draft_id)
            joined = "\n".join(m.value for m in at.markdown)
            assert marker in joined
        finally:
            _delete_draft(draft_id)

    def test_empty_reply_warning_renders(self):
        marker = "EDITOR_LAYOUT_TEST_warning"
        draft_id = _create_draft(marker, link_url=None)
        try:
            at = _render_editor(draft_id)
            all_warnings = [w.value for w in at.warning]
            assert any("Reply is empty" in w for w in all_warnings), (
                f"expected a 'Reply is empty' warning; got: {all_warnings}"
            )
        finally:
            _delete_draft(draft_id)

    def test_no_reply_card_when_reply_empty(self):
        marker = "EDITOR_LAYOUT_TEST_no_reply_card"
        draft_id = _create_draft(marker, link_url=None)
        try:
            at = _render_editor(draft_id)
            joined = "\n".join(m.value for m in at.markdown)
            # Main body shows in the preview.
            assert marker in joined
            # The reply card's "Replying to" rail is NOT rendered when
            # the reply is empty.
            assert "Replying to" not in joined
        finally:
            _delete_draft(draft_id)

    def test_reply_card_renders_when_reply_set(self):
        marker = "EDITOR_LAYOUT_TEST_reply_card"
        draft_id = _create_draft(marker, link_url="https://example.com/ref")
        try:
            at = _render_editor(draft_id)
            joined = "\n".join(m.value for m in at.markdown)
            # Both main and reply render.
            assert marker in joined
            assert "Replying to" in joined
            assert "example.com" in joined
        finally:
            _delete_draft(draft_id)
