"""One-shot cleanup: remove extra cashtags from existing drafts.

The rephrase prompt is being tightened so future drafts don't
include more than one cashtag (X rejects with a 403). This script
back-fills the same rule on existing drafts in the DB so that
Repost / re-Post attempts don't hit the same wall.

For each draft with >1 cashtag in the body:
  - Keep the first cashtag (``$NVDA``).
  - Rewrite the others to a non-cashtag form:
      * ``$175k`` -> ``USD 175k``   (had a digit)
      * ``$NVDA`` -> ``NVDA``      (pure ticker)
  - Same rule for ``link_url`` if it carries cashtags.
  - Update the row in place. Do NOT change status (the existing
    ``posted`` rows reflect real posts that did succeed; we just
    sanitise the body for any future re-use).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from x_auto.config import get_settings  # noqa: E402  (after sys.path tweak)
from x_auto.store.repos import Database  # noqa: E402
from x_auto.utils.text import (  # noqa: E402
    CASHTAG_PATTERN,
    count_cashtags,
    find_cashtags,
)

_KEEP_FIRST = re.compile(r"^.*?(\$[\w]{1,5}\b).*$", re.DOTALL)


def de_cashtag(match: re.Match, keep: str) -> str:
    """If the matched cashtag is the one to keep, return it;
    otherwise rewrite to a non-cashtag form.
    """
    sym = match.group(0)
    if sym == keep:
        return sym
    stripped = sym.lstrip("$")
    if any(c.isdigit() for c in stripped):
        return f"USD {stripped}"
    return stripped


def sanitize(text: str, keep: str) -> str:
    return CASHTAG_PATTERN.sub(lambda m: de_cashtag(m, keep), text)


def main() -> int:
    settings = get_settings()
    db = Database(settings.data_dir / "state.db")

    # Walk every draft, regardless of status. The repo accepts a
    # status filter; passing None returns everything.
    all_drafts = db.list_drafts(status=None, limit=2000)

    fixed: list[tuple[int, int, str]] = []  # (id, old_count, snippet)
    for d in all_drafts:
        body_tags = find_cashtags(d.body)
        if count_cashtags(d.body) <= 1 and (
            not d.link_url or count_cashtags(d.link_url) == 0
        ):
            continue

        new_body = d.body
        if len(body_tags) > 1:
            new_body = sanitize(d.body, keep=body_tags[0])
        new_link = d.link_url
        if d.link_url and count_cashtags(d.link_url) > 0:
            # For the link_url, drop ALL cashtags (it shouldn't have
            # any — the URL is the project referral, not a ticker).
            new_link = CASHTAG_PATTERN.sub(
                lambda m: m.group(0).lstrip("$"), d.link_url
            )

        if (new_body, new_link) != (d.body, d.link_url):
            d.body = new_body
            d.link_url = new_link
            db.update_draft(d)
            fixed.append((d.id, len(body_tags), new_body[:80]))

    print(f"Inspected {len(all_drafts)} drafts.")
    print(f"Cleaned {len(fixed)} drafts:")
    for did, old_count, snippet in fixed:
        print(f"  #{did}: had {old_count} cashtags, now: {snippet!r}")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
