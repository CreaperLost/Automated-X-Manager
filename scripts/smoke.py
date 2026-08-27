"""Headless smoke test that runs the four tabs against recorded fixtures.

Use this for a quick pass/fail when picking the repo up cold. It does
not launch Streamlit and does not make any real network calls. It
exercises:

  - config loading
  - SQLite schema + CRUD
  - URL/cost utilities
  - prompt template rendering
  - AI client (with a stubbed OpenAI SDK)

Usage:
    python scripts/smoke.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _section(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> int:
    failures: list[str] = []

    _section("config")
    from x_auto.config import get_settings
    s = get_settings()
    print(f"  repo_root:  {s.repo_root}")
    print(f"  accounts:   {len(s.accounts)} configured")
    print(f"  projects:   {len(s.projects)} configured")
    print(f"  model:      {s.minimax.model_id}")
    if not s.accounts:
        failures.append("no accounts configured")
    if not s.projects:
        failures.append("no projects configured")

    _section("utils/text")
    from x_auto.utils.text import contains_url, x_char_count
    if not contains_url("see https://x.com"):
        failures.append("contains_url missed https://x.com")
    if contains_url("just text"):
        failures.append("contains_url false positive on 'just text'")
    if x_char_count("https://x.com/" + "a" * 100) != 23:
        failures.append("x_char_count did not apply t.co shortening")
    print("  url detect + char count: OK")

    _section("costs")
    from x_auto.x.costs import estimate_post_cost
    b1 = estimate_post_cost("hello", link_in_reply=True, reply_text="https://x.com")
    b2 = estimate_post_cost("see https://x.com", link_in_reply=False)
    if not (b1.total < b2.total):
        failures.append("link-in-reply should be cheaper than inline URL")
    print(f"  inline=${b2.total:0.3f}  thread=${b1.total:0.3f}  "
          f"saved=${b1.saved:0.3f}")

    _section("db")
    import tempfile

    from x_auto.store.models import Draft
    from x_auto.store.repos import Database
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "state.db")
        db.upsert_account("naval", "1", "Naval")
        new = db.upsert_tweets("naval", [
            {"id": "1", "text": "x", "created_at": "2026-01-01T00:00:00",
             "public_metrics": {}},
        ])
        if new != 1:
            failures.append(f"upsert_tweets expected 1 new, got {new}")
        draft_id = db.create_draft(Draft(body="hello", status="final"))
        db.log_post(draft_id, "post_now", 0.030, "success", "x=1")
        if db.total_session_cost() != 0.030:
            failures.append("post_log total mismatch")
        db.close()
    print("  schema + CRUD + log: OK")

    _section("prompts")
    from x_auto.ai.prompts import DRAFT_SYSTEM, build_rephrase_user
    if "MUST NOT contain any URL" not in DRAFT_SYSTEM:
        failures.append("system prompt missing URL rule")
    msg = build_rephrase_user(
        source_tweet_text="tweet", source_tweet_author="x",
        tone="neutral", num_images=0,
    )
    if "Tone" not in msg:
        failures.append("user prompt missing Tone section")
    print("  system + user prompt: OK")

    _section("ai client (stubbed)")
    from x_auto.ai.client import AIClient
    payload = json.dumps(
        {"main": "x", "reply": "https://x.com", "reasoning": "y"}
    )
    class _M:
        content = payload
    class _C:
        def __init__(self):
            self.message = _M()
    class _R:
        choices = [_C()]
    fake = MagicMock()
    fake.chat.completions.create.return_value = _R()
    with patch("x_auto.ai.client.OpenAI") as cls:
        cls.return_value = fake
        ai = AIClient(s)
        if not ai.configured:
            print("  skipped (MINIMAX_API_KEY not set in env)")
        else:
            try:
                out = ai.generate_draft(system=DRAFT_SYSTEM, user="hi", max_retries=0)
                if out.get("main") != "x":
                    failures.append("ai client returned unexpected payload")
                else:
                    print("  generate_draft roundtrip: OK")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"ai client raised: {exc}")
                print(f"  generate_draft: FAILED ({exc})")

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
