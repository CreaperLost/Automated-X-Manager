"""AI client tests using a stub for the OpenAI SDK."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from x_auto.ai.client import AIClient, DraftGenerationError
from x_auto.ai.prompts import DRAFT_SYSTEM


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.fixture
def configured_settings():
    from x_auto.config import get_settings
    return get_settings()


def test_generate_draft_happy_path(configured_settings):
    payload = json.dumps(
        {
            "main": "Hot take on the latest from X.",
            "reply": "https://acme.com/search",
            "reasoning": "Concise summary with a relevant product link.",
        }
    )
    fake_response = _FakeResponse(payload)
    with patch("x_auto.ai.client.OpenAI") as fake_openai_cls:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        fake_openai_cls.return_value = fake_client
        ai = AIClient(configured_settings)
        out = ai.generate_draft(system=DRAFT_SYSTEM, user="hi")
    assert out["main"] == "Hot take on the latest from X."
    assert out["reply"] == "https://acme.com/search"
    # Verify the OpenAI client was called with the right shape.
    call = fake_client.chat.completions.create.call_args
    assert call.kwargs["response_format"] == {"type": "json_object"}
    msgs = call.kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == DRAFT_SYSTEM
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hi"
    # Verify model is the configured one.
    assert call.kwargs["model"] == configured_settings.minimax.model_id


def test_generate_draft_malformed_json_retries_then_raises(configured_settings):
    bad = "not json"
    with patch("x_auto.ai.client.OpenAI") as fake_openai_cls:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = _FakeResponse(bad)
        fake_openai_cls.return_value = fake_client
        ai = AIClient(configured_settings)
        with pytest.raises(DraftGenerationError):
            ai.generate_draft(system=DRAFT_SYSTEM, user="hi", max_retries=1)
    # Two attempts: original + one retry.
    assert fake_client.chat.completions.create.call_count == 2


def test_generate_draft_missing_required_key(configured_settings):
    payload = json.dumps({"main": "ok", "reply": ""})  # no 'reasoning'
    with patch("x_auto.ai.client.OpenAI") as fake_openai_cls:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = _FakeResponse(payload)
        fake_openai_cls.return_value = fake_client
        ai = AIClient(configured_settings)
        with pytest.raises(DraftGenerationError):
            ai.generate_draft(system=DRAFT_SYSTEM, user="hi", max_retries=0)


def test_unconfigured_client_raises_helpfully():
    from x_auto.config import MinimaxSettings, Settings
    s = Settings(
        repo_root=None,  # type: ignore[arg-type]
        data_dir=None,    # type: ignore[arg-type]
        config_dir=None,  # type: ignore[arg-type]
        accounts=(),
        minimax=MinimaxSettings(
            base_url="https://api.minimax.io/v1",
            model_id="MiniMax-M2.7",
            temperature=0.7,
            max_tokens=400,
            api_key="",  # <- not configured
        ),
        x=None,  # type: ignore[arg-type]
        ui=None,  # type: ignore[arg-type]
    )
    ai = AIClient(s)
    assert not ai.configured
    with pytest.raises(DraftGenerationError, match="MINIMAX_API_KEY"):
        ai.generate_draft(system="x", user="y")


# ---- real-world MiniMax M3 quirks ------------------------------------------

class TestParseAndValidate:
    """Regression coverage for quirks of the MiniMax M-series models
    that the JSON-mode flag doesn't fully suppress."""

    def _parse(self, content: str) -> dict:
        from x_auto.ai.client import _parse_and_validate
        return _parse_and_validate(content)

    def _expect_dge(self, content: str) -> None:
        from x_auto.ai.client import DraftGenerationError
        with pytest.raises(DraftGenerationError):
            self._parse(content)

    def test_strips_think_block_before_json(self):
        # This is the actual shape MiniMax-M3 returns.
        content = (
            "<think>\nThe user wants a short rephrasing in JSON.\n</think>\n"
            '{"main": "Hot take.", "reply": "https://t.co/x", '
            '"reasoning": "Concise."}'
        )
        out = self._parse(content)
        assert out["main"] == "Hot take."
        assert out["reply"] == "https://t.co/x"

    def test_handles_prose_around_json(self):
        # The model sometimes wraps the JSON in chatty preamble.
        content = (
            "Sure! Here is the JSON you asked for:\n"
            '{"main": "ok", "reply": "https://t.co/x", "reasoning": "because"}\n'
            "Hope this helps!"
        )
        out = self._parse(content)
        assert out["main"] == "ok"

    def test_empty_raises(self):
        self._expect_dge("")

    def test_no_json_raises(self):
        self._expect_dge("<think>\nNothing useful here.\n</think>")

    def test_nested_json_is_extracted_correctly(self):
        # The balanced-brace scanner must respect string boundaries.
        content = (
            '{"main": "He said \\"hello\\" to me", '
            '"reply": "https://example.com/{a}", '
            '"reasoning": "ok"}'
        )
        out = self._parse(content)
        assert out["main"] == 'He said "hello" to me'
        assert out["reply"] == "https://example.com/{a}"


class TestRetryOnNoJson:
    """When the model returns only a think-block (no JSON), the
    client should follow up with a strict "JSON only" prompt before
    giving up."""

    def test_retry_with_strict_followup_recovers(self, configured_settings):
        # First call: think-only (the M3-quiet-mode failure shape).
        # Second call: clean JSON.
        think_only = "<think>\nThe user wants a rephrasing.\n</think>"
        good = json.dumps(
            {"main": "Hot take.", "reply": "https://t.co/x", "reasoning": "ok"}
        )
        responses = [
            _FakeResponse(think_only),
            _FakeResponse(good),
        ]
        with patch("x_auto.ai.client.OpenAI") as fake_openai_cls:
            fake_client = MagicMock()
            fake_client.chat.completions.create.side_effect = responses
            fake_openai_cls.return_value = fake_client
            ai = AIClient(configured_settings)
            out = ai.generate_draft(
                system="Reply JSON: {main,reply,reasoning}",
                user="hi",
                max_retries=1,
            )
        assert out["main"] == "Hot take."
        # The retry must include a follow-up user message.
        second_call = fake_client.chat.completions.create.call_args_list[1]
        msgs = second_call.kwargs["messages"]
        assert msgs[-1]["role"] == "user"
        assert "first character" in msgs[-1]["content"] or "JSON" in msgs[-1]["content"]

    def test_gives_up_after_exhausted_retries(self, configured_settings):
        think_only = "<think>\nstill no json\n</think>"
        with patch("x_auto.ai.client.OpenAI") as fake_openai_cls:
            fake_client = MagicMock()
            fake_client.chat.completions.create.return_value = _FakeResponse(
                think_only
            )
            fake_openai_cls.return_value = fake_client
            ai = AIClient(configured_settings)
            with pytest.raises(DraftGenerationError, match="no JSON object"):
                ai.generate_draft(
                    system="x", user="y", max_retries=1
                )
        # Two attempts: original + one retry.
        assert fake_client.chat.completions.create.call_count == 2
