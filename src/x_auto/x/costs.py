"""Cost estimation for X API operations.

Pricing (verified Aug 2026):
  Third-party post read ........... $0.005 per post
  User profile read ............... $0.010 per profile
  Owned read (your own data) ...... $0.001 per resource
  Create plain post ............... $0.015 per post
  Create post with URL inline ..... $0.200 per post
  Delete post ..................... $0.010 per post
  Media upload .................... (free)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.text import contains_url

# Read costs.
COST_READ_POST = 0.005
COST_READ_PROFILE = 0.010
COST_OWNED_READ = 0.001

# Write costs.
COST_POST_PLAIN = 0.015
COST_POST_WITH_URL = 0.200
COST_POST_DELETED = 0.010


@dataclass(frozen=True)
class CostBreakdown:
    main: float
    reply: float
    total: float
    reason: str
    inline_alternative: float | None = None
    saved: float = 0.0


def estimate_post_cost(
    main_text: str,
    *,
    has_image: bool = False,
    link_in_reply: bool = True,
    reply_text: str = "",
) -> CostBreakdown:
    """Estimate the cost of publishing a two-tweet thread.

    `main_text` is the main tweet. `reply_text` is the reply (typically
    just the project URL). If `link_in_reply` is True, the URL goes in
    the reply and the cost is $0.030 ($0.015 + $0.015). If False, the
    URL is in the main tweet and the cost is $0.200.
    """
    if link_in_reply:
        main_cost = COST_POST_PLAIN
        reply_cost = COST_POST_PLAIN if reply_text else 0.0
        inline = COST_POST_WITH_URL if (contains_url(main_text) or contains_url(reply_text)) else None
        saved = (COST_POST_WITH_URL - main_cost - reply_cost) if inline else 0.0
        return CostBreakdown(
            main=main_cost,
            reply=reply_cost,
            total=main_cost + reply_cost,
            reason="link-in-reply (saves $0.170 vs inline URL)" if saved else "link-in-reply",
            inline_alternative=inline,
            saved=saved,
        )
    # Single post, URL inline.
    if contains_url(main_text):
        return CostBreakdown(
            main=COST_POST_WITH_URL,
            reply=0.0,
            total=COST_POST_WITH_URL,
            reason="URL inline ($0.200 surcharge)",
        )
    return CostBreakdown(
        main=COST_POST_PLAIN,
        reply=0.0,
        total=COST_POST_PLAIN,
        reason="plain post",
    )


def estimate_read_cost(num_posts: int, num_profiles: int = 0) -> float:
    """Estimate the cost of reading tweets + user profiles.

    `num_posts` is the count of new (non-duplicate) tweets to read.
    `num_profiles` is the count of distinct handles to look up first.
    """
    return num_posts * COST_READ_POST + num_profiles * COST_READ_PROFILE


class SessionMeter:
    """Running session-spend counter, persisted to post_log on flush."""

    def __init__(self) -> None:
        self._reads_posts = 0
        self._reads_profiles = 0
        self._writes = 0.0

    def add_read_post(self, n: int = 1) -> None:
        self._reads_posts += n

    def add_read_profile(self, n: int = 1) -> None:
        self._reads_profiles += n

    def add_write(self, cost_usd: float) -> None:
        self._writes += cost_usd

    def reads_cost(self) -> float:
        return self._reads_posts * COST_READ_POST + self._reads_profiles * COST_READ_PROFILE

    def total(self) -> float:
        return self.reads_cost() + self._writes

    def summary(self) -> dict[str, float | int]:
        return {
            "posts_read": self._reads_posts,
            "profiles_read": self._reads_profiles,
            "reads_cost_usd": round(self.reads_cost(), 4),
            "writes_cost_usd": round(self._writes, 4),
            "total_cost_usd": round(self.total(), 4),
        }

    def reset(self) -> None:
        self.__init__()
