"""Tests for the visual tweet preview card.

AppTest serialises the app function, so closures don't survive. We
use ``AppTest.from_string`` with a small script template per test —
the kwargs are baked into the script source.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _script_for(**kwargs) -> str:
    """Build a script that calls ``render_tweet_preview(**kwargs)``."""
    # Render the kwargs as Python literals. ``repr(None)`` → "None",
    # ``repr("hi")`` → "'hi'", ``repr(["a"])`` → "['a']" — all valid
    # Python, so we can splat them straight into the script source.
    parts = [f"{k}={v!r}" for k, v in kwargs.items()]
    kw_src = ", ".join(parts)
    return f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"{_SRC}")))

from x_auto.ui.tweet_card import render_tweet_preview

render_tweet_preview({kw_src})
"""


# Resolved at import time so the script template above can put it on
# sys.path for AppTest's isolated execution.
_SRC = str(
    __import__("pathlib").Path(__file__).resolve().parent.parent / "src"
).replace("\\", "\\\\")


def _run(**kwargs) -> AppTest:
    at = AppTest.from_string(_script_for(**kwargs))
    at.run()
    return at


def _all_markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def _all_captions(at: AppTest) -> str:
    return "\n".join(c.value for c in at.caption)


class TestEmptyState:
    def test_empty_body_and_reply_renders_placeholder(self):
        at = _run(body="", reply=None)
        assert "(empty draft)" in _all_captions(at)


class TestBodyOnly:
    def test_body_text_appears(self):
        at = _run(body="Hello world", display_name="Tester")
        assert "Hello world" in _all_markdown(at)

    def test_body_text_is_not_nested_in_html(self):
        """Regression: the body text must render as plain markdown, not
        buried inside a single HTML+CSS block that Streamlit's markdown
        sanitiser can strip. If the body ever appears only as a
        substring of one giant <div>...</div>, this test will fail —
        which is the bug the user reported.
        """
        marker = "BODY_AS_PLAIN_MARKDOWN"
        at = _run(body=marker, display_name="Tester")
        body_elements = [m.value.strip() for m in at.markdown]
        # The body must be its own element (or part of plain markdown
        # at the top level), not a fragment of a multi-line HTML blob.
        assert any(m == marker for m in body_elements), (
            f"expected the body to be a standalone markdown element; "
            f"got elements: {body_elements}"
        )

    def test_handle_and_timestamp(self):
        at = _run(body="x", handle="alice", posted_at=None)
        joined = _all_markdown(at)
        assert "@alice" in joined
        # posted_at is None → "now"
        assert "now" in joined

    def test_no_fake_action_bar(self):
        """The non-interactive '↩ Reply · ♺ Repost · ❤ Like' bar
        was removed in v3 — it implied buttons that didn't exist.
        The reply arrow and the word 'Repost' were both unique to
        the bar; neither should appear in the rendered output."""
        at = _run(body="just a tweet body")
        joined = _all_markdown(at)
        assert "↩" not in joined
        assert "Repost" not in joined


class TestReply:
    def test_reply_renders_both_texts(self):
        at = _run(
            body="Main tweet",
            reply="Reply tweet",
            display_name="Bob",
            handle="bob",
        )
        joined = _all_markdown(at)
        assert "Main tweet" in joined
        assert "Reply tweet" in joined
        # The reply card shows the "Replying to" line.
        assert "Replying to" in joined


class TestLabel:
    def test_custom_label(self):
        at = _run(body="x", label="Custom label")
        assert "Custom label" in _all_markdown(at)


class TestImages:
    def test_missing_image_renders_placeholder_caption(self):
        # Use a path that almost certainly does not exist; AppTest
        # doesn't need a real file for the "missing" branch.
        at = _run(
            body="x",
            image_paths=["C:/this/path/does/not/exist/missing.png"],
        )
        assert "media missing" in _all_captions(at)

    def test_existing_image_does_not_crash(self):
        # A minimal valid 1x1 PNG. st.image may not render it in
        # AppTest (which doesn't run a real browser) but it must not
        # raise. We write under the repo's data/ dir rather than
        # pytest's tmp_path (Windows can be picky about Temp
        # permissions in some sessions).
        from pathlib import Path

        target = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "test_tiny.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                    "890000000a49444154789c63000100000005000100"
                    "0d0a2db40000000049454e44ae426082"
                )
            )
            at = _run(body="x", image_paths=[str(target)])
            assert at.exception == []
        finally:
            try:
                target.unlink()
            except OSError:
                pass


class TestCompactMode:
    """Compact=True is used in the Publish tab's 3-up Drafts grid.

    It must drop the header bar, the action bar, and the reply card
    so the card stays short enough to fit in a 1/3-width column.
    """

    def test_body_renders(self):
        at = _run(body="Compact draft text", compact=True)
        assert "Compact draft text" in _all_markdown(at)

    def test_no_reply_card_even_with_reply(self):
        """compact=True is for tight 1/3-width cards; the reply is
        surfaced via the cost line and the '↪ reply:' caption, not
        as a second card. The reply text shouldn't render at all
        in compact mode."""
        at = _run(
            body="Short main",
            reply="Long reply that would push the card too tall",
            compact=True,
        )
        joined = _all_markdown(at)
        assert "Long reply" not in joined
        # The reply rail marker should also be absent.
        assert "Replying to" not in joined

    def test_no_header_bar(self):
        """compact=True drops the 'Name @handle · timestamp' header.
        The card only shows the body and the image."""
        at = _run(body="Just the body", handle="alice", compact=True)
        joined = _all_markdown(at)
        assert "@alice" not in joined
        # But the body is still there.
        assert "Just the body" in joined
