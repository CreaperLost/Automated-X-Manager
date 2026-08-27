"""Prompt template construction for the 4-step workflow.

The workflow uses two LLM calls, each with its own system prompt
and user-message builder:
  DRAFT_SYSTEM     + build_rephrase_user  → main tweet
  MATCH_SYSTEM     + build_match_user     → project pick + CTA
"""
from x_auto.ai.prompts import (
    DRAFT_SYSTEM,
    MATCH_SYSTEM,
    build_match_user,
    build_rephrase_user,
)


class TestDraftSystem:
    """DRAFT_SYSTEM drives the rephrase call (step 2)."""

    def test_no_url_in_main_body_rule(self):
        assert "MUST NOT contain any URL" in DRAFT_SYSTEM

    def test_x_post_280_constraint(self):
        assert "280" in DRAFT_SYSTEM

    def test_url_surcharge_mentioned(self):
        assert "0.200" in DRAFT_SYSTEM or "0.2" in DRAFT_SYSTEM

    def test_json_schema_for_rephrase(self):
        # The rephrase call returns {main, topic, reasoning}.
        assert '"main"' in DRAFT_SYSTEM
        assert '"topic"' in DRAFT_SYSTEM
        assert '"reasoning"' in DRAFT_SYSTEM

    def test_tone_is_auto_picked(self):
        # v2 simplification: tone is not a UI choice; the AI picks one
        # of {energetic, positive, negative} from the source vibe.
        assert "energetic" in DRAFT_SYSTEM
        assert "positive" in DRAFT_SYSTEM
        assert "negative" in DRAFT_SYSTEM
        # The AI is told to just pick (not ask the user to choose).
        assert "just pick" in DRAFT_SYSTEM or "Do not ask" in DRAFT_SYSTEM

    def test_role_is_rephrasing_not_ghostwriting(self):
        assert "REPHRASING" in DRAFT_SYSTEM or "rephrasing" in DRAFT_SYSTEM
        assert "REPHRASE" in DRAFT_SYSTEM or "rephrase" in DRAFT_SYSTEM

    def test_rephrase_does_not_include_reply_rule(self):
        # The rephrase call only writes the main tweet. The CTA is the
        # match call's job. So DRAFT_SYSTEM should NOT have the old
        # reply-URL rule anymore.
        assert "user's project URL" not in DRAFT_SYSTEM


class TestMatchSystem:
    """MATCH_SYSTEM drives the project-pick + CTA call (step 3)."""

    def test_json_schema_for_match(self):
        # The match call returns {project_name, cta_text, reasoning}.
        assert '"project_name"' in MATCH_SYSTEM
        assert '"cta_text"' in MATCH_SYSTEM
        assert '"reasoning"' in MATCH_SYSTEM

    def test_pick_only_from_list(self):
        assert "Pick ONLY from the project list" in MATCH_SYSTEM
        assert "case-insensitive" in MATCH_SYSTEM

    def test_cta_must_include_project_url(self):
        assert "project's URL" in MATCH_SYSTEM or "project URL" in MATCH_SYSTEM

    def test_no_copy_of_source_url(self):
        # Critical cost invariant: source URL must not leak into the CTA.
        assert "source" in MATCH_SYSTEM and "URL" in MATCH_SYSTEM
        assert "must NOT" in MATCH_SYSTEM or "must not" in MATCH_SYSTEM

    def test_cta_length_cap(self):
        assert "280" in MATCH_SYSTEM

    def test_no_copy_of_source_wording(self):
        # Defends against X duplicate-post detection on the CTA.
        assert "do NOT copy" in MATCH_SYSTEM or "must NOT copy" in MATCH_SYSTEM


class TestBuildRephraseUser:
    """build_rephrase_user assembles the user message for step 2."""

    def test_includes_source_and_author(self):
        msg = build_rephrase_user(
            source_tweet_text="A really good take on X",
            source_tweet_author="naval",
        )
        assert "naval" in msg
        assert "A really good take on X" in msg

    def test_includes_source_url_hint_when_present(self):
        msg = build_rephrase_user(
            source_tweet_text="Check my post",
            source_tweet_author="naval",
            source_url="https://example.com",
        )
        # Source URL is included as a topic hint for the next step.
        assert "https://example.com" in msg
        # And the prompt explicitly says NOT to put it in the main tweet.
        assert "Do NOT" in msg or "do not" in msg

    def test_omits_source_url_when_absent(self):
        msg = build_rephrase_user(
            source_tweet_text="no link",
            source_tweet_author="naval",
            source_url=None,
        )
        assert "Source's CTA" not in msg

    def test_includes_tone(self):
        msg = build_rephrase_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            tone="energetic",
        )
        assert "energetic" in msg

    def test_includes_num_images(self):
        msg = build_rephrase_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            num_images=2,
        )
        assert "2 image" in msg

    def test_includes_extra_instructions(self):
        msg = build_rephrase_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            extra_instructions="mention my open-source project",
        )
        assert "open-source project" in msg

    def test_no_source_falls_back_to_free_write(self):
        msg = build_rephrase_user(
            source_tweet_text="",
            source_tweet_author="",
        )
        assert "Free write" in msg

    def test_no_project_list_in_rephrase_prompt(self):
        # The rephrase call has no project list — that's the match
        # call's job. Make sure we don't accidentally leak it.
        msg = build_rephrase_user(
            source_tweet_text="hi",
            source_tweet_author="x",
        )
        assert "Your projects" not in msg
        assert "project list" not in msg


class TestBuildMatchUser:
    """build_match_user assembles the user message for step 3."""

    def test_includes_project_list(self):
        msg = build_match_user(
            source_tweet_text="Perps are the next big thing",
            source_tweet_author="naval",
            projects=[
                {"name": "Acme", "url": "https://acme.com"},
                {"name": "Helios", "url": "https://helios.dev"},
            ],
        )
        assert "Acme" in msg
        assert "https://acme.com" in msg
        assert "Helios" in msg
        assert "https://helios.dev" in msg

    def test_includes_topic_hint(self):
        msg = build_match_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            topic="DeFi perps",
            projects=[],
        )
        assert "DeFi perps" in msg

    def test_includes_source_url(self):
        msg = build_match_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            source_url="https://source.com",
            projects=[],
        )
        assert "https://source.com" in msg

    def test_handles_empty_projects(self):
        # The match call still works when there are no projects; the
        # LLM will just return no project_name and the fill step
        # leaves the reply empty.
        msg = build_match_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            projects=[],
        )
        assert "(none)" in msg

    def test_no_rephrase_artifacts_in_match_prompt(self):
        # The match call doesn't need the rephrase rules; it just
        # needs the source + project list. Make sure we don't
        # accidentally include the rephrase prompt's hard rules.
        msg = build_match_user(
            source_tweet_text="hi",
            source_tweet_author="x",
            projects=[{"name": "Acme", "url": "https://acme.com"}],
        )
        # The match prompt should mention "Pick" or "best match" — the
        # match call's job.
        assert "Pick" in msg or "best match" in msg
