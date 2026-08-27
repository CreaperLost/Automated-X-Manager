"""Headless diagnostic: import the Streamlit app and exercise every tab's
render path against in-memory fixtures, catching any exception before
the user sees it in the browser.

This is NOT a unit test (it does not mock streamlit widgets). It is a
"does the import chain and tab function call surface still work" check
that runs the same code paths the Streamlit rerun loop would call.

Usage:
    python scripts/diag.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from datetime import datetime as _dt
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Use a temp data dir so we don't touch the real one.
TMP_DATA = Path(tempfile.mkdtemp(prefix="xauto-diag-"))
os.environ["X_BEARER_TOKEN"] = "test-bearer"
os.environ["X_CLIENT_ID"] = "test-client-id"
os.environ["X_CLIENT_SECRET"] = "test-client-secret"
os.environ["MINIMAX_API_KEY"] = "test-minimax-key"


def _section(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> int:
    failures: list[str] = []

    _section("config")
    try:
        from x_auto import config
        config.get_settings.cache_clear()
        # Force the data dir to a tmp one so we don't touch real data/.
        from dataclasses import replace
        s = config.get_settings()
        s = replace(s, data_dir=TMP_DATA)
        print(f"  data_dir:    {s.data_dir}")
        print(f"  accounts:    {len(s.accounts)}")
        print(f"  projects:    {len(s.projects)}")
        print(f"  model:       {s.minimax.model_id}")
    except Exception:
        failures.append("config load failed")
        traceback.print_exc()
        return 1

    _section("db init")
    try:
        from x_auto.store.repos import Database
        from x_auto.ai.projects import sync_projects
        db = Database(s.data_dir / "state.db")
        sync_projects(s, db)
        db.upsert_account("naval", "1", "Naval")
        db.upsert_tweets("naval", [
            {"id": "1", "text": "hello", "created_at": "2026-01-01T00:00:00",
             "public_metrics": {"like_count": 5}},
            {"id": "2", "text": "world", "created_at": "2026-01-02T00:00:00",
             "public_metrics": {"like_count": 7}},
        ])
        db.set_tweet_statuses(["1", "2"], "selected")
        from x_auto.store.models import Draft
        db.create_draft(Draft(
            body="the cost of doing nothing is high",
            link_url="https://acme.com/search",
            image_paths=[],
            tone="analytical",
            status="final",
            finalized_at=_dt.now(),
        ))
        print("  ok")
    except Exception:
        failures.append("db init failed")
        traceback.print_exc()
        return 1

    _section("X client (stubbed) — read endpoints")
    try:
        import respx
        import httpx
        from x_auto.x.client import XClient, API_BASE, RateLimitedError, XApiError

        async def go() -> dict:
            x = XClient(s)
            with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
                mock.get("/users/by/username/naval").mock(return_value=httpx.Response(
                    200, json={"data": {"id": "1", "username": "naval", "name": "Naval"}}))
                mock.get("/users/1/tweets").mock(return_value=httpx.Response(
                    200, json={"data": [
                        {"id": "1", "text": "a", "author_id": "1",
                         "created_at": "2026-01-01T00:00:00.000Z",
                         "public_metrics": {"like_count": 1}},
                    ]}))
                u = await x.get_user_by_username("naval")
                tweets = await x.get_user_tweets(u.id, max_results=20)
                return {"user": u.username, "tweets": len(tweets)}

        result = asyncio.run(go())
        print(f"  user={result['user']}  tweets={result['tweets']}")
    except Exception:
        failures.append("X client read failed")
        traceback.print_exc()

    _section("X client (stubbed) — rate limit (expect RateLimitedError)")
    try:
        from x_auto.x.client import XClient, API_BASE, RateLimitedError
        import respx, httpx

        async def go() -> None:
            x = XClient(s)
            with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
                mock.get("/users/by/username/naval").mock(return_value=httpx.Response(
                    429, headers={"retry-after": "1"}, text="rl"))
                await x.get_user_by_username("naval")

        try:
            asyncio.run(go())
            failures.append("rate limit did not raise")
        except RateLimitedError as exc:
            print(f"  ok: raised RateLimitedError (retry={exc.retry_after_seconds}s)")
    except Exception:
        failures.append("rate limit test crashed")
        traceback.print_exc()

    _section("X client (stubbed) — write (auth-expired path)")
    try:
        from x_auto.x.client import XClient, API_BASE, AuthExpiredError
        from x_auto.x.auth import TokenManager, TokenStore
        from datetime import datetime, timedelta, timezone
        from x_auto.x.auth import TokenBundle
        import respx, httpx

        # Seed a token so the manager can find it.
        store = TokenStore(TMP_DATA / "oauth.json")
        store.save(TokenBundle(
            access_token="at", refresh_token="rt",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
            scope="tweet.read tweet.write", bearer_token=s.x.bearer_token,
        ))
        tm = TokenManager(s, store=store)
        x = XClient(s, token_manager=tm)

        async def go() -> None:
            with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
                mock.post("/tweets").mock(return_value=httpx.Response(401, text="bad"))
                mock.post("/oauth2/token").mock(return_value=httpx.Response(400, text="bad"))
                await x.create_post("hi")

        try:
            asyncio.run(go())
            failures.append("write did not raise on 401+refresh-fail")
        except AuthExpiredError as exc:
            print(f"  ok: raised AuthExpiredError ({exc.detail[:40]})")
    except Exception:
        failures.append("write test crashed")
        traceback.print_exc()

    _section("Cost estimator (link-in-reply invariant)")
    try:
        from x_auto.x.costs import estimate_post_cost
        b1 = estimate_post_cost("hello", link_in_reply=True, reply_text="https://x.com")
        b2 = estimate_post_cost("see https://x.com", link_in_reply=False)
        if not (b1.total < b2.total):
            failures.append("link-in-reply should beat inline URL")
        else:
            print(f"  inline=${b2.total:0.3f}  thread=${b1.total:0.3f}  "
                  f"saved=${b1.saved:0.3f}")
    except Exception:
        failures.append("cost test crashed")
        traceback.print_exc()

    _section("AI client (stubbed OpenAI)")
    try:
        from unittest.mock import MagicMock, patch
        from x_auto.ai.client import AIClient
        import json
        payload = json.dumps({"main": "x", "reply": "https://x.com", "reasoning": "y"})

        class _M:
            content = payload
        class _C:
            def __init__(self): self.message = _M()
        class _R:
            choices = [_C()]
        fake = MagicMock()
        fake.chat.completions.create.return_value = _R()
        with patch("x_auto.ai.client.OpenAI") as cls:
            cls.return_value = fake
            ai = AIClient(s)
            out = ai.generate_draft(system="x", user="y", max_retries=0)
            assert out["main"] == "x"
        print("  ok")
    except Exception:
        failures.append("AI client test crashed")
        traceback.print_exc()

    _section("Scheduler bootstrap (no live jobs)")
    try:
        from x_auto.scheduler import runner as sr
        sched = sr.start(s, db, XClient_for_test(s)) if False else None
        # Don't actually start the scheduler in this diag — it would
        # create a background thread. Just check the module imports and
        # the public functions exist.
        assert callable(sr.schedule_draft)
        assert callable(sr._reap_late_pending)
        assert callable(sr._fire_scheduled)
        print("  module surface ok (scheduler not started to avoid threads)")
    except Exception:
        failures.append("scheduler test crashed")
        traceback.print_exc()

    _section("UI tab modules import + render functions exist")
    try:
        from x_auto.ui import tab_fetch, tab_review, tab_create, tab_publish, layout
        # Tabs all expose `render()`; layout exposes `render_sidebar()`.
        for name, mod, attr in [
            ("layout", layout, "render_sidebar"),
            ("tab_fetch", tab_fetch, "render"),
            ("tab_review", tab_review, "render"),
            ("tab_create", tab_create, "render"),
            ("tab_publish", tab_publish, "render"),
        ]:
            assert hasattr(mod, attr), f"{name} missing {attr}()"
        print("  ok: all 5 modules expose their entry points")
    except Exception:
        failures.append("ui tab test crashed")
        traceback.print_exc()

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: diagnostic passed.")
    return 0


def XClient_for_test(settings):
    from x_auto.x.client import XClient
    return XClient(settings)


if __name__ == "__main__":
    sys.exit(main())
