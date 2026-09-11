"""Semantic media auto-matching utility.

Matches tweet topics and source text against project media descriptions
in the centralized media catalog to automatically recommend the best visual asset.
"""
from __future__ import annotations

import re
from typing import Any

_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "have", "what", "when",
    "your", "will", "more", "about", "there", "their", "here", "just", "into",
    "some", "like", "make", "them", "then", "than", "they", "been", "also",
    "only", "other", "very", "much", "were", "where", "which", "whose", "over",
    "after", "before", "under", "again", "such", "down", "same", "does", "done",
})


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens of length >= 3, excluding stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if len(t) >= 3 and t not in _STOP_WORDS}


def match_best_media(
    project_media: list[dict[str, Any]],
    text: str,
    topic: str = "",
) -> dict[str, Any] | None:
    """Find the best matching media file for a given tweet text and topic.

    Returns the media dictionary with the highest relevance score,
    or None if no media exists or no meaningful match is found.
    """
    if not project_media:
        return None

    topic_tokens = _tokenize(topic)
    text_tokens = _tokenize(text)

    # If neither topic nor text has meaningful tokens, return None
    if not topic_tokens and not text_tokens:
        return None

    clean_topic = topic.strip().lower() if topic else ""

    best_item: dict[str, Any] | None = None
    best_score = 0.0

    for item in project_media:
        desc = (item.get("description") or "").strip()
        filename = (item.get("filename") or "").strip()
        # Stem filename without extension
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename

        desc_tokens = _tokenize(desc)
        fn_tokens = _tokenize(stem)

        score = 0.0

        # Topic tokens matching (weighted 2.0x)
        if topic_tokens:
            score += len(desc_tokens & topic_tokens) * 2.0
            score += len(fn_tokens & topic_tokens) * 2.0

        # Source text tokens matching (weighted 1.0x)
        if text_tokens:
            score += len(desc_tokens & text_tokens) * 1.0
            score += len(fn_tokens & text_tokens) * 1.0

        # Substring / phrase bonus
        clean_desc = desc.lower()
        if clean_topic and len(clean_topic) >= 5 and (clean_topic in clean_desc or clean_desc in clean_topic):
            score += 3.0

        if score > best_score:
            best_score = score
            best_item = item

    if best_score > 0.0 and best_item is not None:
        result = dict(best_item)
        result["match_score"] = round(best_score, 2)
        return result

    return None
