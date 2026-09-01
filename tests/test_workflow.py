"""Tests for the 4-step draft workflow.

The workflow orchestrates two LLM calls (rephrase, then match+CTA)
and two deterministic steps (understand, fill). These tests pin:

  * Step 1 (understand) extracts the source URL via text heuristics.
  * Step 2 (rephrase) uses the {main, topic, reasoning} schema.
  * Step 3 (match + CTA) uses the {project_name, cta_text, reasoning} schema.
  * Step 4 (fill) validates the AI's project pick, falls back if
    unknown, and guarantees the CTA contains the project URL.
  * Two LLM calls happen per generate, in order: rephrase first,
    match second.

We use a fake AIClient that returns a configurable sequence of
results, so each test can exercise a specific path deterministically.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from x_auto.ai.client import AIClient, DraftGenerationError
from x_auto.ai.workflow import DraftWorkflow


def _make_ai(*, rephrase: dict, match: dict) -> MagicMock:
    """A fake AIClient whose generate_draft returns results in order.

    The first call (rephrase) returns ``rephrase``; the second (match)
    returns ``match``. Any further calls also return ``match`` (the
    workflow only makes 2 calls in the happy path).
    """
    ai = MagicMock(spec=AIClient)
    ai.configured = True
    responses = [rephrase, match]
    ai.generate_draft.side_effect = responses
    return ai


PROJECTS = [
    {"name": "Atlas", "url": "https://atlas.example/product",
     "description": "", "tags": []},
    {"name": "Beacon", "url": "https://beacon.example/start",
     "description": "", "tags": []},
    {"name": "Comet", "url": "https://comet.example/join",
     "description": "", "tags": []},
]


class TestUnderstandStep:
    """Step 1 — deterministic URL extraction."""

    def test_extracts_url_from_source(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={"project_name": "Atlas", "cta_text": "Try → https://atlas.example/product", "reasoning": "y"},
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="Check out https://example.com for more",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert result.source_url == "https://example.com"

    def test_no_url_returns_none(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={"project_name": "Atlas", "cta_text": "Try → https://atlas.example/product", "reasoning": "y"},
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="no link here",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert result.source_url is None


class TestRephraseStep:
    """Step 2 — the first LLM call."""

    def test_uses_main_topic_reasoning_schema(self):
        ai = MagicMock(spec=AIClient)
        ai.configured = True
        ai.generate_draft.side_effect = [
            {
                "main": "Fresh take on perps.",
                "topic": "DeFi perps",
                "reasoning": "kept it punchy",
            },
            {
                "project_name": "Comet",
                "cta_text": "Try → https://comet.example/join",
                "reasoning": "matches topic",
            },
        ]
        wf = DraftWorkflow(ai)
        wf.run(
            source_text="Perps are the next big thing",
            source_author="naval",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        # The rephrase call must request the {main, topic, reasoning}
        # schema — not the legacy {main, reply, reasoning}.
        first_call = ai.generate_draft.call_args_list[0]
        assert first_call.kwargs["required_keys"] == ("main", "topic", "reasoning")

    def test_rephrase_output_populates_topic(self):
        ai = _make_ai(
            rephrase={
                "main": "Perps are heating up.",
                "topic": "DeFi perps",
                "reasoning": "energetic tone",
            },
            match={
                "project_name": "Comet",
                "cta_text": "Try → https://comet.example/join",
                "reasoning": "matches",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="Perps",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert result.topic == "DeFi perps"
        assert result.draft.body == "Perps are heating up."

    def test_strips_url_leaked_into_main(self):
        """Defensive: even if the LLM puts a URL in the main body,
        the workflow strips it (cost invariant: $0.200 vs $0.015)."""
        ai = _make_ai(
            rephrase={
                "main": "Fresh take https://leaked.com here",
                "topic": "x",
                "reasoning": "y",
            },
            match={
                "project_name": "Atlas",
                "cta_text": "Try → https://atlas.example/product",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert "https://leaked.com" not in result.draft.body

    def test_original_take_uses_distinct_prompt_and_persists_mode(self):
        ai = _make_ai(
            rephrase={"main": "A new angle.", "topic": "markets", "reasoning": "new"},
            match={"project_name": "Atlas", "cta_text": "Try → https://atlas.example/product", "reasoning": "fit"},
        )
        result = DraftWorkflow(ai).run(
            source_text="Markets are moving", source_author="x",
            source_tweet_id="t-1", projects=PROJECTS,
            writing_mode="original_take",
        )
        first_system = ai.generate_draft.call_args_list[0].kwargs["system"]
        assert "ORIGINAL-TAKE WRITER" in first_system
        assert result.draft.writing_mode == "original_take"
        assert result.writing_mode == "original_take"


class TestMatchStep:
    """Step 3 — the second LLM call."""

    def test_uses_project_name_cta_text_reasoning_schema(self):
        ai = MagicMock(spec=AIClient)
        ai.configured = True
        ai.generate_draft.side_effect = [
            {"main": "x", "topic": "t", "reasoning": "r"},
            {
                "project_name": "Comet",
                "cta_text": "Try → https://comet.example/join",
                "reasoning": "matches",
            },
        ]
        wf = DraftWorkflow(ai)
        wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        second_call = ai.generate_draft.call_args_list[1]
        assert second_call.kwargs["required_keys"] == (
            "project_name", "cta_text", "reasoning"
        )

    def test_match_receives_project_list(self):
        """The match call's user message must include every project."""
        ai = MagicMock(spec=AIClient)
        ai.configured = True
        ai.generate_draft.side_effect = [
            {"main": "x", "topic": "t", "reasoning": "r"},
            {
                "project_name": "Atlas",
                "cta_text": "Try → https://atlas.example/product",
                "reasoning": "y",
            },
        ]
        wf = DraftWorkflow(ai)
        wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        match_user = ai.generate_draft.call_args_list[1].kwargs["user"]
        for p in PROJECTS:
            assert p["name"] in match_user
            assert p["url"] in match_user

    def test_chosen_project_name_matches_list(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "Comet",
                "cta_text": "Try → https://comet.example/join",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert result.project_name == "Comet"
        assert result.project_url == "https://comet.example/join"


class TestFillStep:
    """Step 4 — deterministic validation + URL injection."""

    def test_falls_back_when_ai_returns_unknown_project(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "Imaginary",
                "cta_text": "Try here",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        # Falls back to the first project in the list.
        assert result.project_name == "Atlas"
        assert result.fallback_used is True

    def test_falls_back_when_ai_returns_no_project_name(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "",
                "cta_text": "Try here",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert result.project_name == "Atlas"
        assert result.fallback_used is True

    def test_case_insensitive_project_match(self):
        """Project names can come back in any case from the LLM."""
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "comet",  # lowercase
                "cta_text": "Try → https://comet.example/join",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert result.project_name == "Comet"
        assert result.fallback_used is False

    def test_injects_url_when_cta_missing_it(self):
        """If the AI's CTA doesn't include the chosen project URL,
        the fill step appends it. Defensive guarantee for the
        link-in-reply cost invariant."""
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "Atlas",
                "cta_text": "Worth a look",  # URL missing
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert "https://atlas.example/product" in result.cta_text
        assert result.draft.link_url is not None
        assert "https://atlas.example/product" in result.draft.link_url

    def test_does_not_double_inject_url_when_present(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "Atlas",
                "cta_text": "Try → https://atlas.example/product",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        # The URL appears exactly once in the CTA.
        assert result.cta_text.count("https://atlas.example/product") == 1

    def test_no_projects_leaves_cta_empty(self):
        """If the CSV is empty, the match step is skipped; the
        reply stays empty and the user sees a warning in the UI."""
        ai = MagicMock(spec=AIClient)
        ai.configured = True
        ai.generate_draft.side_effect = [
            {"main": "x", "topic": "t", "reasoning": "r"},
        ]
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=[],
        )
        # Only the rephrase call is made.
        assert ai.generate_draft.call_count == 1
        assert result.project_name == ""
        assert result.project_url == ""
        assert result.cta_text == ""
        assert result.draft.link_url is None


class TestTwoCallOrder:
    """Pin that the workflow calls rephrase first, match second."""

    def test_rephrase_called_before_match(self):
        ai = MagicMock(spec=AIClient)
        ai.configured = True
        call_order: list[str] = []

        def _fake_generate_draft(*, system: str, user: str, **kw):
            # The system prompt differs: rephrase uses DRAFT_SYSTEM
            # (mentions "rephrased tweet"), match uses MATCH_SYSTEM
            # (mentions "PROJECT MATCHMAKER").
            if "PROJECT MATCHMAKER" in system:
                call_order.append("match")
                return {
                    "project_name": "Atlas",
                    "cta_text": "Try → https://atlas.example/product",
                    "reasoning": "y",
                }
            call_order.append("rephrase")
            return {"main": "x", "topic": "t", "reasoning": "r"}

        ai.generate_draft.side_effect = _fake_generate_draft
        wf = DraftWorkflow(ai)
        wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
        )
        assert call_order == ["rephrase", "match"]


class TestDraftPersistenceShape:
    """The returned Draft row has the shape the Create tab persists."""

    def test_draft_has_all_required_fields(self):
        ai = _make_ai(
            rephrase={"main": "Main body", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "Atlas",
                "cta_text": "Try → https://atlas.example/product",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="tweet-42",
            projects=PROJECTS,
        )
        d = result.draft
        assert d.source_tweet_id == "tweet-42"
        assert d.body == "Main body"
        assert d.link_url is not None
        assert "atlas.example" in d.link_url
        assert d.status == "draft"  # ready to persist as a draft row
        assert d.tone == ""

    def test_draft_carries_image_paths(self):
        ai = _make_ai(
            rephrase={"main": "x", "topic": "t", "reasoning": "r"},
            match={
                "project_name": "Atlas",
                "cta_text": "Try → https://atlas.example/product",
                "reasoning": "y",
            },
        )
        wf = DraftWorkflow(ai)
        result = wf.run(
            source_text="x",
            source_author="x",
            source_tweet_id="t-1",
            projects=PROJECTS,
            image_paths=["/cache/foo.png"],
        )
        assert result.draft.image_paths == ["/cache/foo.png"]


class TestErrorPropagation:
    """If the LLM fails, the error surfaces to the caller."""

    def test_rephrase_failure_propagates(self):
        ai = MagicMock(spec=AIClient)
        ai.configured = True
        ai.generate_draft.side_effect = DraftGenerationError("rephrase boom")
        wf = DraftWorkflow(ai)
        with pytest.raises(DraftGenerationError, match="rephrase boom"):
            wf.run(
                source_text="x",
                source_author="x",
                source_tweet_id="t-1",
                projects=PROJECTS,
            )
