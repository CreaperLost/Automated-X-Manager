"""Visual preview of a tweet (+ optional reply), styled like the X timeline.

This is a presentation-only helper. It is used in the Create tab (live,
reflects the current edits) and Queue (final drafts, drafts,
paraphrase preview, posted history). The function is pure presentation
— it does not write to the DB or call the X API.

Design notes
------------
The previous version hand-rolled an HTML+CSS layout using ``display:flex``
and other modern CSS properties. Streamlit's markdown renderer sanitises
that CSS and the result was a broken card: the body text was hidden
while ``st.image`` (rendered as a separate Streamlit element) still
showed. This rewrite uses Streamlit-native primitives — ``st.container``,
``st.columns``, plain ``st.markdown`` — and limits raw HTML to two
small, well-supported pieces (the circular avatar, the reply
connector). That trades some pixel-fidelity for reliable rendering on
every browser.

Two modes
---------
* Default (``compact=False``) — full preview with header bar, action
  bar, reply card, etc. Used in Create and in the per-final-draft card.
* ``compact=True`` — minimal preview: no header, no action bar, no
  reply card. Used inside the 3-up Drafts grid where horizontal real
  estate is tight.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

# X-style palette (close enough for a preview).
_AVATAR_BG = "#1d9bf0"
_AVATAR_BG_REPLY = "#536471"
_TEXT_PRIMARY = "#0f1419"
_TEXT_SECONDARY = "#536471"
_RULE = "#eff3f4"
_LINK = "#1d9bf0"


def render_tweet_preview(
    body: str,
    reply: str | None = None,
    image_paths: list[str] | None = None,
    *,
    display_name: str = "You",
    handle: str = "you",
    posted_at: datetime | None = None,
    label: str = "📱 Preview — how it'll look on X",
    compact: bool = False,
) -> None:
    """Render a card that looks like a tweet, plus an optional reply card.

    Empty inputs collapse to a small "(empty draft)" caption so the
    caller doesn't have to guard against missing text.

    ``compact=True`` strips the header bar (name/handle/timestamp) and
    the reply card; it is meant for the tight 1/3-width Drafts grid.
    """
    body = (body or "").strip()
    reply = (reply or "").strip()
    image_paths = list(image_paths or [])

    if not body and not reply and not image_paths:
        st.caption("(empty draft)")
        return

    if not compact:
        st.markdown(f"**{label}**")

    with st.container(border=True):
        if compact:
            _render_one_compact(
                body,
                image_paths,
                display_name=display_name,
                handle=handle,
            )
            return

        _render_one_full(
            body,
            image_paths,
            display_name=display_name,
            handle=handle,
            posted_at=posted_at,
        )
        if reply:
            _render_reply_connector()
            _render_one_full(
                reply,
                image_paths=None,
                display_name=display_name,
                handle=handle,
                posted_at=None,
                small=True,
                is_reply=True,
            )


# --- Full preview (Create tab, per-final-draft card) ------------------------

def _render_one_full(
    text: str,
    image_paths: list[str] | None,
    *,
    display_name: str,
    handle: str,
    posted_at: datetime | None,
    small: bool = False,
    is_reply: bool = False,
) -> None:
    avatar_px = 32 if small else 44
    initial = (display_name or handle or "?").strip()[:1].upper() or "?"
    avatar_bg = _AVATAR_BG if not is_reply else _AVATAR_BG_REPLY
    timestamp = _fmt_ts(posted_at) if posted_at else "now"

    # Streamlit columns give us a reliable two-column layout (no flexbox).
    # The avatar is a small styled <div>; everything else is plain markdown.
    cols = st.columns([1, 9])
    with cols[0]:
        _render_avatar(initial, avatar_bg, avatar_px)
    with cols[1]:
        # Header line: name (bold) · @handle · timestamp.
        if is_reply:
            st.markdown(
                f"<span style='color:{_TEXT_PRIMARY};font-weight:700;'>"
                f"{_html_escape(display_name)}</span> "
                f"<span style='color:{_TEXT_SECONDARY};'>"
                f"@{_html_escape(handle)} · "
                f"Replying to "
                f"<span style='color:{_LINK};'>@{_html_escape(handle)}</span> · "
                f"{_html_escape(timestamp)}</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"**{_html_escape(display_name)}** "
                f"<span style='color:{_TEXT_SECONDARY};'>"
                f"@{_html_escape(handle)} · "
                f"{_html_escape(timestamp)}</span>",
                unsafe_allow_html=True,
            )

        # Body — use plain markdown. Newlines become <br> via markdown.
        if text:
            st.markdown(text)
        else:
            st.markdown(
                "<i style='color:#aab8c2'>(empty)</i>",
                unsafe_allow_html=True,
            )

    # Images live below the row, full container width. ``st.image``
    # reliably handles the file I/O and browser rendering.
    if image_paths and not small:
        for p in image_paths:
            try:
                path = Path(p)
                if path.exists() and path.is_file():
                    st.image(str(path), use_container_width=True)
                else:
                    st.caption(f"🖼  (image missing: {Path(p).name})")
            except (OSError, ValueError):
                st.caption(f"🖼  (image unreadable: {Path(p).name})")

    # The earlier revision rendered a fake action bar
    # ("↩ Reply · ♺ Repost · ❤ Like") at the bottom of every full
    # preview. It implied functionality that didn't exist (the
    # buttons weren't wired) and the avatar + body + reply card
    # already convey "this is how it'll look on X". Removed in v3.


# --- Compact preview (3-up Drafts grid) -------------------------------------

def _render_one_compact(
    text: str,
    image_paths: list[str] | None,
    *,
    display_name: str,
    handle: str,
) -> None:
    """Tight preview for 1/3-width draft cards. No header, no action bar.

    Just the body text and an image thumbnail (if any). The card lives
    inside the outer draft-card container, so we skip the inner border
    by rendering without ``st.container``.
    """
    if text:
        # Cap visible length so a long draft doesn't blow up the grid row.
        shown = text if len(text) <= 200 else text[:200].rstrip() + "…"
        st.markdown(shown)
    else:
        st.markdown("<i style='color:#aab8c2'>(empty)</i>", unsafe_allow_html=True)

    if image_paths:
        for p in image_paths:
            try:
                path = Path(p)
                if path.exists() and path.is_file():
                    st.image(str(path), use_container_width=True)
                else:
                    st.caption(f"🖼  (image missing: {Path(p).name})")
            except (OSError, ValueError):
                st.caption(f"🖼  (image unreadable: {Path(p).name})")



# --- Shared building blocks -------------------------------------------------

def _render_avatar(initial: str, bg_color: str, size_px: int) -> None:
    """A circular avatar with a single letter. The only flexbox-y HTML we keep.

    ``display:flex`` is supported by Streamlit's markdown on modern
    browsers, and the circle itself is just a rounded square; we tested
    this in the wild and it renders reliably.
    """
    font_px = max(10, int(size_px * 0.45))
    st.markdown(
        f"<div style='background:{bg_color};color:#fff;width:{size_px}px;"
        f"height:{size_px}px;border-radius:50%;display:flex;"
        f"align-items:center;justify-content:center;font-weight:700;"
        f"font-size:{font_px}px;line-height:1;'>{_html_escape(initial)}</div>",
        unsafe_allow_html=True,
    )


def _render_reply_connector() -> None:
    st.markdown(
        f"<div style='margin:0 0 0 21px;border-left:2px solid {_RULE};"
        f"height:18px;'></div>",
        unsafe_allow_html=True,
    )


def _fmt_ts(value: datetime) -> str:
    now = datetime.now()
    delta = (now - value).total_seconds()
    if delta < 0:
        return value.strftime("%Y-%m-%d %H:%M")
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86_400:
        return f"{int(delta // 3600)}h"
    if delta < 86_400 * 7:
        return f"{int(delta // 86_400)}d"
    return value.strftime("%Y-%m-%d")


def _html_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
