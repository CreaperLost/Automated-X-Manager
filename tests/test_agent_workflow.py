"""Tests for the autonomous AgentDraftWorkflow with niche tone enforcement."""
from __future__ import annotations

from unittest.mock import MagicMock

from x_auto.ai.client import AIClient
from x_auto.ai.workflow import AgentDraftWorkflow

PROJECTS = [
    {"name": "Hyperliquid", "url": "https://app.hyperliquid.xyz/join/AERO", "description": "", "tags": []},
    {"name": "Claude", "url": "https://claude.ai", "description": "", "tags": []},
]


def test_agent_workflow_generates_both_options():
    ai = MagicMock(spec=AIClient)
    ai.configured = True

    # 1. Option A (Rephrase)
    # 2. Option B (Original Take)
    # 3. Match + CTA
    ai.generate_draft.side_effect = [
        {
            "main": "Massive leap for decentralized perps. Liquidity is surging!",
            "topic": "DeFi perps",
            "reasoning": "High energy and conviction",
        },
        {
            "main": "Centralized exchanges can no longer compete with on-chain speed.",
            "topic": "DEX vs CEX",
            "reasoning": "Bold provocative take",
        },
        {
            "project_name": "Hyperliquid",
            "cta_text": "Trade with zero slippage → https://app.hyperliquid.xyz/join/AERO",
            "reasoning": "Best match for perp DEX topic",
        },
    ]

    wf = AgentDraftWorkflow(ai, niche="crypto")
    result = wf.run(
        source_text="Hyperliquid hits record daily volume of $5B!",
        source_author="crypto_news",
        source_tweet_id="t123",
        projects=PROJECTS,
    )

    assert result.option_a_main == "Massive leap for decentralized perps. Liquidity is surging!"
    assert result.option_b_main == "Centralized exchanges can no longer compete with on-chain speed."
    assert result.project_name == "Hyperliquid"
    assert "https://app.hyperliquid.xyz/join/AERO" in result.cta_text
    assert result.niche == "crypto"


def test_agent_workflow_tone_enforcement():
    ai = MagicMock(spec=AIClient)
    ai.configured = True

    ai.generate_draft.side_effect = [
        {"main": "Take A", "topic": "AI", "reasoning": "Alarmist"},
        {"main": "Take B", "topic": "AI", "reasoning": "Existential threat"},
        {"project_name": "Claude", "cta_text": "Prepare → https://claude.ai", "reasoning": "AI match"},
    ]

    wf = AgentDraftWorkflow(ai, niche="ai")
    wf.run(
        source_text="New autonomous agents can now replace junior developers entirely.",
        source_author="tech_insider",
        source_tweet_id="t999",
        projects=PROJECTS,
    )

    # Verify that the system prompt passed to generate_draft included AI_TONE_RULE
    calls = ai.generate_draft.call_args_list
    assert len(calls) == 3
    # First call (Option A) system prompt
    assert "FEAR MONGERING" in calls[0].kwargs["system"]
    # Second call (Option B) system prompt
    assert "FEAR MONGERING" in calls[1].kwargs["system"]


def test_agent_workflow_crypto_tone_enforcement():
    ai = MagicMock(spec=AIClient)
    ai.configured = True

    ai.generate_draft.side_effect = [
        {"main": "Take A", "topic": "Crypto", "reasoning": "Bullish"},
        {"main": "Take B", "topic": "Crypto", "reasoning": "Positive"},
        {"project_name": "Hyperliquid", "cta_text": "Trade → https://app.hyperliquid.xyz/join/AERO", "reasoning": "Fit"},
    ]

    wf = AgentDraftWorkflow(ai, niche="crypto")
    wf.run(
        source_text="Bitcoin breaks new highs as institutional inflows accelerate.",
        source_author="btc_daily",
        source_tweet_id="t888",
        projects=PROJECTS,
    )

    calls = ai.generate_draft.call_args_list
    assert "ENERGETIC and POSITIVE" in calls[0].kwargs["system"]
    assert "ENERGETIC and POSITIVE" in calls[1].kwargs["system"]


def test_agent_workflow_strips_urls_from_main_posts():
    ai = MagicMock(spec=AIClient)
    ai.configured = True

    ai.generate_draft.side_effect = [
        {
            "main": "Check this out https://spam.link and see the future!",
            "topic": "Crypto",
            "reasoning": "Rephrase",
        },
        {
            "main": "Leaked URL https://bad.link shouldn't be in main body.",
            "topic": "Crypto",
            "reasoning": "Original take",
        },
        {
            "project_name": "Hyperliquid",
            "cta_text": "Join now: https://app.hyperliquid.xyz/join/AERO",
            "reasoning": "Fit",
        },
    ]

    wf = AgentDraftWorkflow(ai, niche="crypto")
    result = wf.run(
        source_text="Major announcement today.",
        source_author="announcements",
        source_tweet_id="t555",
        projects=PROJECTS,
    )

    assert "https://spam.link" not in result.option_a_main
    assert "https://bad.link" not in result.option_b_main
    assert "Check this out and see the future!" in result.option_a_main


def test_agent_workflow_ai_strips_cashtag_and_guarantees_cta_url():
    ai = MagicMock(spec=AIClient)
    ai.configured = True

    ai.generate_draft.side_effect = [
        {
            "main": "Your job is not safe, wake up. $AI",
            "topic": "AI",
            "reasoning": "Alarmist",
        },
        {
            "main": "Everyone is sleeping on autonomous systems. $AI",
            "topic": "AI",
            "reasoning": "Provocative",
        },
        {
            "project_name": "Claude",
            "cta_text": "The people who master this will win. Get ahead:",
            "reasoning": "Fit",
        },
    ]

    wf = AgentDraftWorkflow(ai, niche="ai")
    result = wf.run(
        source_text="AI is replacing software engineers faster than predicted.",
        source_author="tech_ai",
        source_tweet_id="t777",
        projects=[{"name": "Claude", "url": "https://claude.ai", "description": "", "tags": []}],
    )

    # 1. $AI must be cleanly stripped
    assert "$AI" not in result.option_a_main
    assert "$AI" not in result.option_b_main
    assert result.option_a_main == "Your job is not safe, wake up."
    assert result.option_b_main == "Everyone is sleeping on autonomous systems."

    # 2. CTA reply post MUST include the URL
    assert "https://claude.ai" in result.cta_text
