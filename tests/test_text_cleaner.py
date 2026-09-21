"""Tests for humanized text cleaner."""
from __future__ import annotations

from x_auto.utils.text import clean_humanized_text


def test_clean_humanized_text_em_dash() -> None:
    raw = "AI agents are evolving — and fast."
    cleaned = clean_humanized_text(raw)
    assert "—" not in cleaned
    assert "–" not in cleaned
    assert "--" not in cleaned
    assert cleaned == "AI agents are evolving, and fast."


def test_clean_humanized_text_double_hyphen() -> None:
    raw = "Stop waiting -- build production workflows now."
    cleaned = clean_humanized_text(raw)
    assert "--" not in cleaned
    assert cleaned == "Stop waiting, build production workflows now."


def test_clean_humanized_text_leading_dash() -> None:
    raw = "— Here is why everyone is falling behind."
    cleaned = clean_humanized_text(raw)
    assert not cleaned.startswith(",")
    assert not cleaned.startswith("—")
    assert "Here is why" in cleaned


def test_clean_humanized_text_multiple_dashes() -> None:
    raw = "Fast — reliable — ready for scale."
    cleaned = clean_humanized_text(raw)
    assert "—" not in cleaned
    assert cleaned == "Fast, reliable, ready for scale."


def test_clean_humanized_text_strips_trailing_ai_ticker() -> None:
    raw = "Wake up before the automation wave hits your desk. $AI"
    cleaned = clean_humanized_text(raw, niche="ai")
    assert "$AI" not in cleaned
    assert cleaned == "Wake up before the automation wave hits your desk."


def test_clean_humanized_text_converts_inline_ai_ticker() -> None:
    raw = "The whole $AI industry is evolving rapidly."
    cleaned = clean_humanized_text(raw, niche="ai")
    assert "$AI" not in cleaned
    assert cleaned == "The whole AI industry is evolving rapidly."


def test_format_cta_reply_attaches_and_preserves_url() -> None:
    from x_auto.utils.text import format_cta_reply, x_char_count

    url = "https://www.udemy.com/course/ai-mastery-leverage-ai-to-10x-your-income/?referralCode=19B8F7988BA894DCA6BE&couponCode=MT250923G1"
    copy = "AI models are dropping back to back. Master this stuff before you get left behind. Get ahead:"

    res = format_cta_reply(copy, url, niche="ai")
    assert url in res
    assert res.startswith("AI models are dropping back to back.")
    assert x_char_count(res) <= 280


def test_format_cta_reply_never_drops_url_on_long_copy() -> None:
    from x_auto.utils.text import format_cta_reply, x_char_count

    url = "https://www.udemy.com/course/langchain-langgraph-build-production-ai-agents-in-python/?referralCode=58EBADD324DA28BCEA9A"
    long_copy = "Word " * 60  # ~300 chars of copy

    res = format_cta_reply(long_copy, url, niche="ai")
    # The URL MUST be preserved
    assert url in res
    # The whole tweet must satisfy X's weighted char count
    assert x_char_count(res) <= 280
