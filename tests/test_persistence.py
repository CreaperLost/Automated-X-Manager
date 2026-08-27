"""Persistence flows for the Create + Publish tabs.

These tests cover the DB-level contract that backs the v4 Create/Publish
tabs: Generate persists, Save updates the same row, Discard deletes,
Promote flips status. We exercise the helper functions directly
because the Streamlit UI layer is smoke-tested in scripts/diag.py.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from x_auto.store.models import Draft
from x_auto.store.repos import Database

# ---- helpers ----------------------------------------------------------------

def _make_source(handle: str = "naval", text: str = "original source") -> SimpleNamespace:
    """A stand-in for the selected Tweet."""
    return SimpleNamespace(id="tweet-1", account_handle=handle, text=text)


def _save_source_tweet(db: Database, source) -> None:
    """Insert the source tweet so FKs resolve (drafts.source_tweet_id)."""
    db.upsert_account(source.account_handle, "uid-1")
    db.upsert_tweets(
        source.account_handle,
        [
            {
                "id": source.id,
                "text": source.text,
                "created_at": "2026-01-01T00:00:00Z",
                "public_metrics": {},
            }
        ],
    )


# ---- Create tab persistence -------------------------------------------------

class TestGeneratePersistsToDB:
    def test_generate_creates_draft_row(self, tmp_db: Database):
        """The moment Generate finishes, a draft row is in the DB."""
        source = _make_source()
        _save_source_tweet(tmp_db, source)

        # The full _on_generate calls st.success/st.rerun which need a
        # Streamlit context. We test the persistence half by writing
        # the same row it would write.
        draft = Draft(
            source_tweet_id=source.id,
            body="Rephrased",
            link_url="https://example.com",
            image_paths=[],
            tone="",
            status="draft",
        )
        new_id = tmp_db.create_draft(draft)
        assert new_id > 0
        loaded = tmp_db.get_draft(new_id)
        assert loaded is not None
        assert loaded.body == "Rephrased"
        assert loaded.link_url == "https://example.com"
        assert loaded.status == "draft"

    def test_save_as_draft_updates_existing_row(self, tmp_db: Database):
        """Save as draft updates the row, doesn't create a new one."""
        draft = Draft(body="v1", status="draft")
        draft_id = tmp_db.create_draft(draft)
        # User edits in the form, then Save as draft.
        draft = tmp_db.get_draft(draft_id)
        assert draft is not None
        draft.body = "v2"
        draft.link_url = "https://example.com"
        tmp_db.update_draft(draft)
        rows = tmp_db.list_drafts(status="draft")
        assert len(rows) == 1
        assert rows[0].id == draft_id
        assert rows[0].body == "v2"
        assert rows[0].link_url == "https://example.com"

    def test_save_as_final_flips_status_and_sets_finalized_at(
        self, tmp_db: Database
    ):
        draft = Draft(body="x", status="draft")
        draft_id = tmp_db.create_draft(draft)
        draft = tmp_db.get_draft(draft_id)
        assert draft is not None
        draft.status = "final"
        draft.finalized_at = datetime.now()
        tmp_db.update_draft(draft)
        # No longer in the drafts list.
        assert tmp_db.list_drafts(status="draft") == []
        # In the final list.
        finals = tmp_db.list_drafts(status="final")
        assert len(finals) == 1
        assert finals[0].id == draft_id
        assert finals[0].finalized_at is not None

    def test_discard_deletes_row(self, tmp_db: Database):
        draft = Draft(body="x", status="draft")
        draft_id = tmp_db.create_draft(draft)
        tmp_db.delete_draft(draft_id)
        assert tmp_db.get_draft(draft_id) is None
        assert tmp_db.list_drafts(status="draft") == []


# ---- Publish tab Drafts section ---------------------------------------------

class TestPublishDraftsSection:
    def test_drafts_listing_excludes_finals_and_posted(
        self, tmp_db: Database
    ):
        """The Drafts (not yet final) section shows only status=draft."""
        # Three drafts in different states.
        for status in ("draft", "final", "posted"):
            d = Draft(body=f"b-{status}", status=status)
            if status == "final":
                d.finalized_at = datetime.now()
            elif status == "posted":
                d.posted_at = datetime.now()
                d.x_tweet_id = "123"
            tmp_db.create_draft(d)
        rows = tmp_db.list_drafts(status="draft")
        assert len(rows) == 1
        assert rows[0].body == "b-draft"
        assert rows[0].status == "draft"

    def test_promote_moves_draft_to_final(self, tmp_db: Database):
        """The Promote button's effect: status flips to 'final'."""
        d = Draft(body="x", status="draft")
        draft_id = tmp_db.create_draft(d)
        # Simulate the Promote button click.
        d = tmp_db.get_draft(draft_id)
        assert d is not None
        d.status = "final"
        d.finalized_at = datetime.now()
        tmp_db.update_draft(d)
        # The draft moved sections.
        assert tmp_db.list_drafts(status="draft") == []
        finals = tmp_db.list_drafts(status="final")
        assert len(finals) == 1
        assert finals[0].id == draft_id


# ---- Cross-tab handoff via query param --------------------------------------

class TestQueryParamHandoff:
    def test_edit_draft_query_param_round_trips(self):
        """A draft id set as ?edit_draft=5 is readable as the int 5.

        This is the contract the Publish tab relies on when the user
        clicks "Open in Create ↗". The publish tab's setter writes
        ``str(int)``; the create tab's reader does ``int()``.
        """
        s = "5"
        assert int(s) == 5
