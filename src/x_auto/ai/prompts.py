"""Prompt templates for the MiniMax-powered draft generator.

The 4-step workflow in ``ai/workflow.py`` uses two distinct LLM calls,
each with its own system prompt and user-message builder:

  REPHRASE_SYSTEM  + build_rephrase_user   → step 2: write the main tweet
  MATCH_SYSTEM     + build_match_user      → step 3: pick project + write CTA

The two-step split keeps each LLM call focused on one job and makes
the per-step output easy to inspect in tests.

The assistant must return JSON. The required keys depend on the call:
  - Rephrase: {main, topic, reasoning}
  - Match:    {project_name, cta_text, reasoning}
"""
from __future__ import annotations

from typing import Any

# ---- system prompts --------------------------------------------------------

DRAFT_SYSTEM = """You are a REPHRASING ASSISTANT for X (Twitter) posts. The user
has selected a source tweet and wants a rephrased version of it that
captures the same idea in their own words, while staying within the
free-user character limit. You are not inventing a new take; you are
restating the source's idea in the user's voice.

Hard rules:

1. The MAIN tweet body MUST be ≤ 280 characters. The free X plan caps
   posts at 280 chars; longer tweets are rejected or trimmed. Aim for
   220–260 chars to leave headroom for the user to edit. If the source
   tweet is long, COMPRESS the idea; do not pad.

2. The MAIN tweet body MUST NOT contain any URL — not the source's URL,
   not any URL. URLs are NOT your concern in this call (a separate
   step writes the reply tweet with the URL). This is a hard cost
   invariant: a URL in the main body costs $0.200 instead of $0.015.

3. REPHRASE, don't copy. The output should read as the user's own
   take, not a rephrasing so close to the source that it triggers X's
   duplicate-post detection. Restructure the sentence, swap a few
   words, take a different angle on the same idea. Keep the source's
   core point; lose its wording.

4. One idea per tweet. No hashtag spam. No emoji-stuffing. No
   clickbait ("You won't believe…", numbered lists, all-caps).

5. AT MOST ONE cashtag. A cashtag is any ``$`` followed by 1–5
   ticker characters (letters or digits), e.g. ``$NVDA``,
   ``$BTC``, ``$25K``. X rejects a post with two or more cashtags
   with a 403 error — the post is not created and the round-trip
   is wasted. If your rephrase needs to mention multiple tickers,
   write one with a ``$`` and the rest as plain text (e.g.
   "split across $NVDA and MRVL markets"). The same rule applies
   to dollar amounts: ``$175k`` and ``$25k`` are cashtags too. If
   you need a dollar figure, write it as "175k USD" or "USD 25k"
   instead. This is a hard cost-and-correctness invariant, not a
   style preference.

5. TONE: pick exactly one of these three tones and write the whole
   tweet in it: "energetic", "positive", or "negative". Choose the one
   that best matches the source tweet's vibe — a punchy/hot-take
   source gets "energetic", a hopeful/encouraging source gets
   "positive", a critical/skeptical source gets "negative". Do not ask
   the user; just pick.

X API constraints (verified Aug 2026) — your awareness, not your job:
- A post containing a URL costs $0.200 (13.3× a plain $0.015 post).
- A post can carry up to 4 images, each ≤ 5 MB.
- Posts are limited to 280 characters by default.
- Bearer tokens cannot write; user-context OAuth is required.

For a deeper reference (endpoints, rate limits, common gotchas), the
agent's tool list includes data/x_skill.md.

Return a single JSON object with this exact shape — no extra keys, no
markdown fences:

{
  "main":      "<the rephrased tweet, ≤280 chars, no URL>",
  "topic":     "<one short phrase: the source's topic, used by the next step>",
  "reasoning": "<one short sentence: the tone you picked and why this framing>"
}
"""


MATCH_SYSTEM = """You are a PROJECT MATCHMAKER and CTA WRITER for X (Twitter) posts.

The user has a list of their own projects (each with a name and a
referral URL). They want to post a tweet about a source tweet and
have the reply (a separate post) point at the user's own project
that is most relevant to the source's topic.

Your job, in order:

1. Read the source tweet and the topic hint.
2. Look at the user's project list (name + URL for each).
3. Pick the project whose topic is closest to the source. If nothing
   clearly fits, pick the first project in the list.
4. Write a SHORT call-to-action (CTA) that:
     a) reads as the user's voice, not as a copy of the source's CTA,
     b) includes the chosen project's URL (verbatim, from the list),
     c) is ≤ 280 characters (it's a tweet),
     d) does NOT copy the source's wording or repeat its URL.

Rules:
- Pick ONLY from the project list provided. The "project_name" must
  match an entry exactly (case-insensitive). Never invent a name.
- The "cta_text" must include the chosen project's URL (the one you
  picked). If you forget, the app will append it; aim to get it right.
- The "cta_text" must NOT include any URL from the source tweet.
- The "cta_text" must NOT copy the source's wording.
- Keep the CTA short. Examples of the right shape (don't copy these):
      "Try now → https://app.ondoperps.xyz/?ref=44CXB4"
      "Worth a look if you're trading perps → https://entropy.io/?r=aero"
      "20% off your first trade → https://app.perpl.xyz/trade?ref=..."

X API constraints (verified Aug 2026) — your awareness, not your job:
- A post containing a URL costs $0.200 (13.3× a plain $0.015 post).
- Posts are limited to 280 characters by default.

Return a single JSON object with this exact shape — no extra keys, no
markdown fences:

{
  "project_name": "<exact name of the chosen project from the list>",
  "cta_text":     "<short CTA + the chosen project's URL, ≤280 chars>",
  "reasoning":    "<one short sentence: why this project fits the source's topic>"
}
"""


# ---- user-message builders -------------------------------------------------

def build_rephrase_user(
    *,
    source_tweet_text: str,
    source_tweet_author: str,
    source_url: str | None = None,
    tone: str = "",
    num_images: int = 0,
    extra_instructions: str = "",
) -> str:
    """Assemble the user message for the rephrase call (step 2).

    No project list here — the rephrase task is about voice and
    freshness, not about which project to point at. The match step
    (step 3) handles project selection with a separate LLM call.
    """
    lines: list[str] = []
    if source_tweet_text:
        lines.append("## Inspiration tweet")
        lines.append(f"By @{source_tweet_author}:")
        lines.append("")
        lines.append("> " + source_tweet_text.replace("\n", "\n> "))
        lines.append("")
    if source_url:
        lines.append("## Source's CTA (the URL the source points at)")
        lines.append(
            "This is a topic hint for the next step (project matching). "
            "Do NOT include this URL in the main tweet body — it would "
            "trigger the $0.200 URL-surcharge. Use it only as context."
        )
        lines.append("")
        lines.append(source_url)
        lines.append("")
    if tone:
        lines.append(f"## Tone: {tone}")
    if num_images:
        lines.append(f"## Attachments: {num_images} image(s) will be attached.")
        lines.append(
            "You do not need to describe the images; just write a caption that works."
        )
    if extra_instructions:
        lines.append("## Extra instructions from the user")
        lines.append(extra_instructions)
    if not lines:
        lines.append("Free write. No inspiration, no context, no tone constraint.")
    return "\n".join(lines).strip()


def build_match_user(
    *,
    source_tweet_text: str,
    source_tweet_author: str,
    source_url: str | None = None,
    topic: str = "",
    projects: list[dict[str, Any]],
) -> str:
    """Assemble the user message for the match + CTA call (step 3)."""
    lines: list[str] = []
    if source_tweet_text:
        lines.append("## Inspiration tweet")
        lines.append(f"By @{source_tweet_author}:")
        lines.append("")
        lines.append("> " + source_tweet_text.replace("\n", "\n> "))
        lines.append("")
    if source_url:
        lines.append("## Source's CTA (the URL the source points at)")
        lines.append(source_url)
        lines.append("")
    if topic:
        lines.append("## Topic hint (from the rephrase step)")
        lines.append(topic)
        lines.append("")
    lines.append("## Your projects (pick the best match)")
    if not projects:
        lines.append("(none)")
    else:
        for p in projects:
            lines.append(f"- **{p['name']}** — URL: {p['url']}")
    lines.append("")
    lines.append(
        "Pick the project whose topic is closest to the source. "
        "Write a fresh CTA — do NOT copy the source's wording."
    )
    return "\n".join(lines).strip()
