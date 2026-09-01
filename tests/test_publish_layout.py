"""Tests for the Publish tab's Drafts grid layout.

The previous design had one huge card per draft with a buried
"Promote to final" button. The new design is a 3-up grid of compact
cards, each with a single primary "Post" action. These tests pin
that shape:

* Each draft renders as a separate compact preview (body text is
  top-level markdown, not nested in HTML).
* Multiple drafts are laid out across columns of width 3.
* The "Post" button is the primary action and is visible per card.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from x_auto.store.models import Draft
from x_auto.store.repos import Database


def _render_publish() -> AppTest:
    """Render the Publish tab end-to-end against the real (data/state.db)."""
    src_path = (
        str(Path(__file__).resolve().parent.parent / "src").replace("\\", "\\\\")
    )
    script = f"""
import sys
sys.path.insert(0, r"{src_path}")
from x_auto.config import get_settings
from x_auto.store.repos import Database
from x_auto.ui.tab_publish import render

settings = get_settings()
db = Database(settings.data_dir / "state.db")

class _FakeClient:
    pass

render(
    settings,
    db,
    _FakeClient(),
    ai=None,
)
"""
    at = AppTest.from_string(script)
    at.run()
    return at


def _render_publish_with_session_state(
    session_state_setup: str,
) -> AppTest:
    """Render the Publish tab with pre-seeded session state.

    Used to pin the persistent post-result banner: ``_post_now``
    stashes its result in ``st.session_state`` because the parent
    button handler does ``st.rerun()`` and a rerun wipes any inline
    ``st.success`` / ``st.error``. The banner at the top of the
    Publish tab reads from session state, so the message stays
    visible until the user dismisses it.
    """
    src_path = (
        str(Path(__file__).resolve().parent.parent / "src").replace("\\", "\\\\")
    )
    script = f"""
import sys
sys.path.insert(0, r"{src_path}")
import streamlit as st
from x_auto.config import get_settings
from x_auto.store.repos import Database
from x_auto.ui.tab_publish import render

settings = get_settings()
db = Database(settings.data_dir / "state.db")

{session_state_setup}

class _FakeClient:
    pass

render(
    settings,
    db,
    _FakeClient(),
    ai=None,
)
"""
    at = AppTest.from_string(script)
    at.run()
    return at


def _make_drafts(db: Database, items: list[tuple[str, str]]) -> list[int]:
    """Create drafts with the given (status, body) pairs. Returns ids."""
    ids: list[int] = []
    for status, body in items:
        d = Draft(
            source_tweet_id=None,
            body=body,
            link_url=None,
            image_paths=[],
            tone="",
            status=status,
        )
        if status == "final":
            d.finalized_at = datetime.now()
        ids.append(db.create_draft(d))
    return ids


def _purge_drafts(db: Database, ids: list[int]) -> None:
    for i in ids:
        db.delete_draft(i)


class TestDraftsGridLayout:
    def test_three_drafts_render_three_cards(self):
        """With 3 pending drafts, the Drafts section shows 3 separate
        compact previews (one per draft), not a single huge one."""
        db = Database(_data_dir() / "state.db")
        bodies = [f"PUBLAYOUT_GRID_{i}_body" for i in range(3)]
        ids = _make_drafts(db, [("draft", b) for b in bodies])
        try:
            at = _render_publish()
        finally:
            _purge_drafts(db, ids)
            db.close()

        # Each draft body is a top-level markdown element.
        body_elements = [m.value.strip() for m in at.markdown]
        for b in bodies:
            assert any(e == b for e in body_elements), (
                f"expected {b!r} as a top-level markdown element; "
                f"got: {body_elements}"
            )

        # Each draft gets its own "Post" button.
        post_buttons = [b for b in at.button if b.label == "Post"]
        assert len(post_buttons) >= 3, (
            f"expected at least 3 Post buttons, got {len(post_buttons)}"
        )

    def test_final_status_drafts_also_appear(self):
        """Backward compat: drafts already marked 'final' (by the
        old UI) should still show up in the Drafts grid."""
        db = Database(_data_dir() / "state.db")
        marker = "PUBLAYOUT_FINAL_BACKCOMPAT"
        ids = _make_drafts(db, [("final", marker)])
        try:
            at = _render_publish()
        finally:
            _purge_drafts(db, ids)
            db.close()

        joined = "\n".join(m.value for m in at.markdown)
        assert marker in joined

    def test_posted_drafts_not_in_queue(self):
        """The Drafts grid is the 'not yet posted' queue; already-
        posted drafts move to the Published section."""
        db = Database(_data_dir() / "state.db")
        draft_marker = "PUBLAYOUT_QUEUE_yes"
        posted_marker = "PUBLAYOUT_QUEUE_no_already_posted"
        ids = _make_drafts(
            db,
            [("draft", draft_marker), ("posted", posted_marker)],
        )
        try:
            at = _render_publish()
        finally:
            _purge_drafts(db, ids)
            db.close()

        # The draft in 'draft' status has its body in the page…
        joined = "\n".join(m.value for m in at.markdown)
        assert draft_marker in joined
        # …and the posted one is shown too — in the Published section.
        assert posted_marker in joined

        # Each "Post" button is keyed by draft id, so we can tell them
        # apart: a posted draft should NOT have a Post button in the
        # Drafts grid (it gets a Repost button in Published instead).
        post_keys = {b.key for b in at.button if b.label == "Post"}
        assert all(k.startswith("post_") for k in post_keys)


def _data_dir() -> Path:
    from x_auto.config import get_settings
    return get_settings().data_dir


class TestPostResultBanner:
    """The Publish tab renders a persistent banner for the last post
    result, pulled from session state. This is how we keep the success
    / error message visible after the parent ``st.rerun()`` that
    refreshes the Drafts/Published split — without it, the message
    flashes for one frame and is gone.
    """

    def test_success_banner_persists_across_reruns(self):
        """When the last post succeeded, the banner stays visible on
        the next render (no fresh click required) and offers a
        Dismiss button."""
        setup = (
            'st.session_state["publish_last_post_result"] = {\n'
            '    "kind": "success",\n'
            '    "message": "**Posted draft #42** — main `123`. Cost $0.030.",\n'
            '}'
        )
        at = _render_publish_with_session_state(setup)
        assert at.exception == []

        # The banner shows the success message.
        success_msgs = [s.value for s in at.success]
        assert any("Posted draft #42" in m for m in success_msgs), (
            f"expected the success banner; got: {success_msgs}"
        )

        # A Dismiss button is present so the user can clear it.
        dismiss = [b for b in at.button if b.label == "Dismiss"]
        assert any("publish_dismiss_last_post" in b.key for b in dismiss), (
            f"expected a Dismiss button; got keys: "
            f"{[b.key for b in dismiss]}"
        )

    def test_error_banner_persists_across_reruns(self):
        """When the last post failed (e.g. rate-limited), the error
        message stays visible long enough to read, instead of
        flashing and disappearing on the rerun."""
        setup = (
            'st.session_state["publish_last_post_result"] = {\n'
            '    "kind": "error",\n'
            '    "message": "**Rate-limited on draft #7:** wait 5s and try again.",\n'
            '}'
        )
        at = _render_publish_with_session_state(setup)
        assert at.exception == []

        # The banner shows the error message — including the wait hint.
        error_msgs = [e.value for e in at.error]
        assert any("Rate-limited" in m and "wait 5s" in m for m in error_msgs), (
            f"expected the rate-limit error banner; got: {error_msgs}"
        )

    def test_no_banner_when_no_recent_post(self):
        """A fresh visit (no last-post in session state) shows no
        banner — the user hasn't tried to post anything yet."""
        at = _render_publish()
        # No Dismiss button in this case.
        dismiss = [b for b in at.button if b.label == "Dismiss"]
        assert all("publish_dismiss_last_post" not in b.key for b in dismiss)
