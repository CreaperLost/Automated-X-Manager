"""Queue UI for drafts and posted history.

Lifecycle (single path, no intermediate "final" state):
  1. **Drafts** (status="draft" or "final") — compact 3-up grid with
     one-click **Post**, plus **Open in Create** and **Discard**.
  2. **Published** — status="posted" rows; **Repost** / **Paraphrase & Repost**.
  3. **Recent post log** — last 20 log entries.

The previous version had a separate "Final drafts" section that required
a "Promote to final" click before posting. That was a friction the
user flagged: it was hidden in a huge card, the verb was confusing, and
it added a step that didn't actually do anything the Post button
couldn't do itself. The flow is now **Draft → Post** in one click.

Every preview uses ``ui/tweet_card.py`` and is shared with the Create
tab. The compact mode is used here for the 1/3-width Draft cards.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from ..ai.client import AIClient, DraftGenerationError
from ..ai.projects import list_projects
from ..ai.workflow import DraftWorkflow
from ..config import Settings
from ..store.models import Draft
from ..store.repos import Database
from ..utils.text import contains_url, validate_post_body
from ..x.client import AuthExpiredError, RateLimitedError, XApiError, XClient
from ..x.costs import estimate_post_cost
from ..x.publish import publish_draft_sync
from .tweet_card import render_tweet_preview

# Session-state keys (kept short and tab-prefixed).
_KEY_PARAPHRASE = "publish_paraphrase_preview"  # {draft_id: {body, reply, reasoning}}
_KEY_LAST_POST = "publish_last_post_result"  # {"kind": "success"|"error", "message": str}

# Statuses that are still in the queue.
# "final" is included for backward compatibility with older drafts that
# were marked final before the flow was simplified.
_PENDING_STATUSES = ("draft", "final")


def render(
    settings: Settings,
    db: Database,
    x_client: XClient,
    ai: AIClient,
) -> None:
    st.header("Queue")
    # No global caption — each section has its own one-liner below.

    # Persistent post-result banner. ``st.success`` / ``st.error`` are
    # ephemeral: the parent button handler does ``st.rerun()`` to refresh
    # the Drafts/Published split, and the rerun wipes the message. We
    # stash the result in session state and render it here so the user
    # can actually read it.
    last = st.session_state.get(_KEY_LAST_POST)
    if last:
        if last["kind"] == "success":
            st.success(last["message"])
        else:
            st.error(last["message"])
        if st.button("Dismiss", key="publish_dismiss_last_post", type="secondary"):
            st.session_state.pop(_KEY_LAST_POST, None)
            st.rerun()

    # ---- 1. Drafts (the queue) — 3-up grid, compact, one-click Post -------
    st.markdown("### Drafts")
    st.caption(
        "Post a draft now. The ⋯ popover has edit / discard."
    )
    pending = _list_pending(db, limit=60)
    if not pending:
        st.caption("No drafts in the queue. Generate one in **Create**.")
    else:
        _render_drafts_grid(settings, db, x_client, pending)

    # ---- 2. Published (repost / paraphrase) --------------------------------
    st.markdown("---")
    st.markdown("### Published")
    st.caption("Re-post or paraphrase any of your past tweets.")
    published = db.list_drafts(status="posted", limit=50)
    if not published:
        st.caption("Nothing published yet.")
    else:
        _render_published_grid(
            settings, db, x_client, ai, published
        )

    # ---- 3. Recent post log -----------------------------------------------
    st.markdown("---")
    st.markdown("### Recent post log")
    for entry in db.recent_log(limit=20):
        st.caption(
            f"{_fmt_dt(_parse_dt(entry.get('created_at')))} · "
            f"#{entry.get('draft_id')} · {entry.get('action')} · "
            f"{entry.get('result')} · ${(entry.get('cost_usd') or 0):0.4f}"
        )


# --- Drafts (the queue) -----------------------------------------------------

def _list_pending(db: Database, *, limit: int) -> list[Draft]:
    """Return drafts in the queue: status in ('draft', 'final')."""
    rows = db.list_drafts(status=None, limit=200)
    return [d for d in rows if d.status in _PENDING_STATUSES][:limit]


def _render_drafts_grid(
    settings: Settings,
    db: Database,
    x_client: XClient,
    drafts: list[Draft],
) -> None:
    """3-up grid of compact draft cards. Each card has a clear "Post"
    primary action and a popover for everything else."""
    # Render the cards in chunks of 3. Streamlit's columns take
    # ownership of vertical space for the whole row, so each row is its
    # own ``st.columns(3)`` call. This keeps the grid honest even when
    # cards have different heights.
    for row_start in range(0, len(drafts), 3):
        chunk = drafts[row_start:row_start + 3]
        cols = st.columns(3, gap="small")
        for col, d in zip(cols, chunk, strict=False):
            with col:
                _render_draft_card_compact(settings, db, x_client, d)


def _render_draft_card_compact(
    settings: Settings,
    db: Database,
    x_client: XClient,
    draft: Draft,
) -> None:
    """A 1/3-width draft card with a single primary "Post" action."""
    cost = estimate_post_cost(
        draft.body,
        has_image=bool(draft.image_paths),
        link_in_reply=bool(draft.link_url),
        reply_text=draft.link_url or "",
    )

    with st.container(border=True):
        # Header line: draft id + source handle (and reply URL when set).
        # One line, stronger weight via st.markdown so the dim caption
        # styling doesn't wash it out.
        if draft.source_tweet_id:
            src = db.get_tweet(draft.source_tweet_id)
            handle_part = (
                f" · @{_html_escape(src.account_handle)}" if src else ""
            )
        else:
            handle_part = ""
        if draft.link_url:
            st.markdown(
                f"**#{draft.id}**{handle_part} · "
                f"↪ `{_truncate(draft.link_url, 36)}`"
            )
        else:
            st.markdown(f"**#{draft.id}**{handle_part}")

        # Compact body preview (text + image thumbnail). Skips the
        # header bar / action rail that the full preview has.
        render_tweet_preview(
            draft.body,
            reply=None,  # Reply is shown in the post action only — too
                         # tall for a 1/3 card. Cost preview covers it.
            image_paths=draft.image_paths,
            display_name="You",
            handle="you",
            posted_at=None,
            label="",
            compact=True,
        )

        # Cost preview — kept short for the compact card.
        st.caption(
            f"${cost.total:0.3f} total · "
            f"${cost.saved:0.3f} saved vs inline URL"
        )

        # Primary action.
        if st.button(
            "Post",
            key=f"post_{draft.id}",
            type="primary",
            use_container_width=True,
        ):
            _post_now(settings, db, x_client, draft)
            st.rerun()

        # Popover for edit and discard.
        with st.popover("⋯", use_container_width=True, help="More actions"):
            if st.button(
                "Open in Create ↗",
                key=f"publish_open_create_{draft.id}",
                use_container_width=True,
            ):
                st.query_params["edit_draft"] = str(draft.id)
                st.session_state["requested_view"] = "Create"
                st.rerun()

            if st.button(
                "Discard",
                key=f"publish_discard_{draft.id}",
                use_container_width=True,
            ):
                db.delete_draft(draft.id)
                st.warning(f"Draft #{draft.id} deleted.")
                st.rerun()


def _post_now(
    settings: Settings,
    db: Database,
    x_client: XClient,
    draft: Draft,
) -> None:
    """Post a draft immediately.

    Pre-flight: validate the main + reply bodies for X's hard
    rules (≤280 chars, no URL in the main, at most one cashtag).
    If a check fails, the post is rejected locally and we surface a
    sticky error banner — saves a 4xx round-trip and the
    corresponding cost.

    Stashes success / error in session state (under
    ``_KEY_LAST_POST``) instead of calling ``st.success`` /
    ``st.error`` directly, because the caller does ``st.rerun()``
    right after to refresh the Drafts/Published split and a rerun
    wipes any inline message. The persistent banner at the top of
    Queue reads from session state and shows the result
    until the user dismisses it.
    """
    # ---- Pre-flight: catch X's strictest rules before paying ----
    preflight: list[str] = []
    for err in validate_post_body(draft.body, role="main"):
        preflight.append(f"**Main tweet** — {err.message}  \n_Hint: {err.hint}_")
    if draft.link_url:
        for err in validate_post_body(
            draft.link_url, role="reply", allow_url=True
        ):
            preflight.append(f"**Reply tweet** — {err.message}  \n_Hint: {err.hint}_")
    if preflight:
        db.log_post(
            draft.id, "post_now", None, "preflight_failed",
            " | ".join(e.split(" — ", 1)[-1].split("  \n")[0] for e in preflight)[:500],
        )
        st.session_state[_KEY_LAST_POST] = {
            "kind": "error",
            "message": (
                f"**Post blocked on draft #{draft.id}** — failed "
                f"pre-flight checks:\n\n" + "\n\n".join(preflight)
            ),
        }
        return

    with st.spinner("Posting…"):
        try:
            result = publish_draft_sync(settings, db, x_client, draft)
        except AuthExpiredError as exc:
            db.log_post(
                draft.id, "post_now", None, "auth_error", exc.detail
            )
            st.session_state[_KEY_LAST_POST] = {
                "kind": "error",
                "message": (
                    f"**Auth error on draft #{draft.id}:** {exc.detail}. "
                    "Re-authorize via the X OAuth flow and retry."
                ),
            }
            return
        except RateLimitedError as exc:
            db.log_post(
                draft.id, "post_now", None, "rate_limited", exc.detail
            )
            st.session_state[_KEY_LAST_POST] = {
                "kind": "error",
                "message": (
                    f"**Rate-limited on draft #{draft.id}:** "
                    f"{exc.detail}. Wait {exc.retry_after_seconds}s "
                    "and try again."
                ),
            }
            return
        except XApiError as exc:
            db.log_post(
                draft.id, "post_now", None, "failed", exc.detail
            )
            st.session_state[_KEY_LAST_POST] = {
                "kind": "error",
                "message": (
                    f"**X API error on draft #{draft.id}** "
                    f"({exc.status}): {exc.detail[:300]}"
                ),
            }
            return
    st.session_state[_KEY_LAST_POST] = {
        "kind": "success",
        "message": (
            f"**Posted draft #{draft.id}** — main `{result.x_tweet_id}`"
            f"{f' + reply `{result.x_reply_id}`' if result.x_reply_id else ''}."
            f" Cost ${result.cost_usd:0.3f}."
        ),
    }


# --- Published card (repost / paraphrase) ------------------------------------

def _render_published_grid(
    settings: Settings,
    db: Database,
    x_client: XClient,
    ai: AIClient,
    published: list[Draft],
) -> None:
    """3-up grid of compact published cards. Paraphrase preview lives in
    a popover on each card so the grid layout stays honest.
    """
    for row_start in range(0, len(published), 3):
        chunk = published[row_start:row_start + 3]
        cols = st.columns(3, gap="small")
        for col, d in zip(cols, chunk, strict=False):
            with col:
                _render_published_card_compact(
                    settings, db, x_client, ai, d
                )


def _render_published_card_compact(
    settings: Settings,
    db: Database,
    x_client: XClient,
    ai: AIClient,
    original: Draft,
) -> None:
    """A 1/3-width published card. Repost is inline; Paraphrase opens a
    popover with the full editor + preview (too tall for the card).
    """
    with st.container(border=True):
        # Header: id + posted-at + cost, with the "View on X" link
        # inlined on the same line. st.markdown for stronger weight
        # (the old st.caption washed out the bold).
        x_url = (
            f"https://x.com/i/status/{original.x_tweet_id}"
            if original.x_tweet_id
            else None
        )
        head = (
            f"**#{original.id}** · {_fmt_dt(original.posted_at)} · "
            f"${(original.cost_usd or 0):0.3f}"
        )
        if x_url:
            head += f" · [View on X ↗]({x_url})"
        st.markdown(head)

        # Source handle (small, never wraps awkwardly).
        source = None
        if original.source_tweet_id:
            source = db.get_tweet(original.source_tweet_id)
            if source:
                st.caption(f"From **@{_html_escape(source.account_handle)}**")

        # Body preview (compact — no reply card inside, too tall).
        render_tweet_preview(
            original.body,
            reply=None,
            image_paths=original.image_paths,
            display_name="You",
            handle="you",
            posted_at=None,
            label="",
            compact=True,
        )

        # Reply URL (short, blue-ish) — if present.
        if original.link_url:
            st.caption(f"↪ reply: `{_truncate(original.link_url, 40)}`")

        # Two action buttons: Repost (inline) + Paraphrase (popover).
        col_repost, col_paraphrase = st.columns(2)
        with col_repost:
            if st.button(
                "Repost",
                key=f"repost_{original.id}",
                use_container_width=True,
            ):
                _do_repost(settings, db, x_client, original)
                st.rerun()
        with col_paraphrase:
            can_paraphrase = bool(original.source_tweet_id) and bool(original.link_url)
            with st.popover(
                "Paraphrase ↻",
                use_container_width=True,
                disabled=not can_paraphrase,
                help=(
                    "Re-calls the AI with the same source + project to "
                    "produce a fresh take, then post it."
                    if can_paraphrase
                    else "Needs a source tweet and a project URL to paraphrase."
                ),
            ):
                _render_paraphrase_popover(
                    settings, db, x_client, ai, original
                )


def _render_paraphrase_popover(
    settings: Settings,
    db: Database,
    x_client: XClient,
    ai: AIClient,
    original: Draft,
) -> None:
    """The Paraphrase & Repost flow, rendered inside a popover so the
    surrounding 3-col grid stays clean.

    Two states:
    1. No preview yet in session state → show "Generate" button.
    2. Preview exists → show editable body + reply, live preview, and
       "Post this version" / "Cancel" buttons.
    """
    previews = st.session_state.get(_KEY_PARAPHRASE) or {}
    has_preview = original.id in previews

    if not has_preview:
        st.caption(
            f"Generate a fresh take on draft **#{original.id}** using the "
            f"same source tweet + project URL."
        )
        if st.button(
            "Generate",
            key=f"paraphrase_gen_{original.id}",
            type="primary",
            use_container_width=True,
        ):
            _start_paraphrase_preview(settings, db, ai, original)
            st.rerun()
        return

    # Preview exists — show the editor + preview + actions.
    _render_paraphrase_preview(settings, db, x_client, ai, original)


def _do_repost(
    settings: Settings,
    db: Database,
    x_client: XClient,
    original: Draft,
) -> None:
    """Clone the published draft as a new draft and publish it."""
    new_draft = Draft(
        source_tweet_id=original.source_tweet_id,
        body=original.body,
        link_url=original.link_url,
        quote_tweet_id=None,
        image_paths=list(original.image_paths),
        tone=original.tone,
        status="draft",  # _publish_draft will flip to 'posted'
    )
    new_id = db.create_draft(new_draft)
    new_draft.id = new_id
    try:
        result = publish_draft_sync(settings, db, x_client, new_draft)
        st.success(
            f"Reposted as draft #{new_id} — main `{result.x_tweet_id}`, "
            f"reply `{result.x_reply_id}`. Cost ${result.cost_usd:0.3f}."
        )
    except AuthExpiredError as exc:
        st.error(f"Auth error: {exc.detail}")
        db.log_post(new_id, "repost", None, "auth_error", exc.detail)
    except XApiError as exc:
        st.error(f"X API error: {exc.detail}")
        db.log_post(new_id, "repost", None, "failed", exc.detail)


def _start_paraphrase_preview(
    settings: Settings,
    db: Database,
    ai: AIClient,
    original: Draft,
) -> None:
    """Call the AI to paraphrase the original draft, store the preview."""
    source = db.get_tweet(original.source_tweet_id) if original.source_tweet_id else None
    if source is None or not original.link_url:
        return

    projects = list_projects(db)
    chosen = next(
        (p for p in projects if p["url"] == original.link_url),
        None,
    )
    if chosen is None:
        chosen = {
            "name": "the project",
            "url": original.link_url,
            "description": "",
            "tags": [],
        }

    workflow = DraftWorkflow(ai)
    try:
        wf_result = workflow.run(
            source_text=source.text,
            source_author=source.account_handle,
            source_tweet_id=original.source_tweet_id,
            projects=[chosen],
            extra_instructions="",
            image_paths=list(original.image_paths),
        )
    except DraftGenerationError as exc:
        st.error(f"Paraphrase failed: {exc}")
        return

    new_body = wf_result.draft.body
    new_reply = (wf_result.draft.link_url or "").strip() or original.link_url or ""
    previews = st.session_state.setdefault(_KEY_PARAPHRASE, {})
    previews[original.id] = {
        "body": new_body,
        "reply": new_reply,
        "reasoning": wf_result.rephrase_reasoning or wf_result.match_reasoning,
    }


def _render_paraphrase_preview(
    settings: Settings,
    db: Database,
    x_client: XClient,
    ai: AIClient,
    original: Draft,
) -> None:
    previews = st.session_state.get(_KEY_PARAPHRASE) or {}
    preview = previews.get(original.id)
    if not preview:
        return

    st.markdown("**Paraphrase preview** — edit, then post:")

    col_edit, col_preview = st.columns([3, 2])
    with col_edit:
        edited_body = st.text_area(
            "Main (rephrased)",
            value=preview["body"],
            key=f"paraphrase_body_{original.id}",
            height=100,
        )
        edited_reply = st.text_input(
            "Reply (CTA + your project link)",
            value=preview["reply"],
            key=f"paraphrase_reply_{original.id}",
        )
        st.caption(f"Reasoning: {preview.get('reasoning', '')}")

        if contains_url(edited_body):
            st.error(
                "Main tweet contains a URL — would cost $0.200 instead of $0.015. "
                "Move the URL to the reply field below."
            )

        cost = estimate_post_cost(
            edited_body,
            has_image=bool(original.image_paths),
            link_in_reply=bool(edited_reply.strip()),
            reply_text=edited_reply,
        )
        st.caption(
            f"Cost preview: ${cost.main:0.3f} + ${cost.reply:0.3f} = "
            f"${cost.total:0.3f}"
        )

        col_post, col_cancel = st.columns(2)
        with col_post:
            if st.button(
                "Post this version",
                key=f"paraphrase_post_{original.id}",
                type="primary",
                use_container_width=True,
            ):
                _post_paraphrased(
                    settings, db, x_client, original, edited_body, edited_reply
                )
                previews.pop(original.id, None)
                if not previews:
                    st.session_state.pop(_KEY_PARAPHRASE, None)
                st.rerun()
        with col_cancel:
            if st.button(
                "Cancel",
                key=f"paraphrase_cancel_{original.id}",
                use_container_width=True,
            ):
                previews.pop(original.id, None)
                if not previews:
                    st.session_state.pop(_KEY_PARAPHRASE, None)
                st.rerun()

    with col_preview:
        render_tweet_preview(
            edited_body,
            reply=edited_reply or None,
            image_paths=original.image_paths,
            display_name="You",
            handle="you",
            posted_at=datetime.now(),
            label="📱 Preview",
        )


def _post_paraphrased(
    settings: Settings,
    db: Database,
    x_client: XClient,
    original: Draft,
    new_body: str,
    new_reply: str,
) -> None:
    new_draft = Draft(
        source_tweet_id=original.source_tweet_id,
        body=new_body.strip(),
        link_url=new_reply.strip() or None,
        quote_tweet_id=None,
        image_paths=list(original.image_paths),
        tone="",
        status="draft",
    )
    new_id = db.create_draft(new_draft)
    new_draft.id = new_id
    try:
        result = publish_draft_sync(settings, db, x_client, new_draft)
        st.success(
            f"Posted: main `{result.x_tweet_id}`, "
            f"reply `{result.x_reply_id}`. Cost ${result.cost_usd:0.3f}."
        )
    except AuthExpiredError as exc:
        st.error(f"Auth error: {exc.detail}")
        db.log_post(new_id, "paraphrase", None, "auth_error", exc.detail)
    except XApiError as exc:
        st.error(f"X API error: {exc.detail}")
        db.log_post(new_id, "paraphrase", None, "failed", exc.detail)


# --- shared -----------------------------------------------------------------

def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _html_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_dt(value) -> str:
    """Relative time for events <7 days, absolute date for older.

    Mirrors the convention in :func:`x_auto.ui.tab_review._fmt_dt` so
    cards and the live preview line up. Handles both naive (SQLite)
    and tz-aware (test fixture) inputs.
    """
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    if getattr(value, "tzinfo", None) is not None:
        from datetime import UTC

        now = datetime.now(UTC)
    else:
        now = datetime.now()
    delta_s = (now - value).total_seconds()
    if delta_s < 0:
        return value.strftime("%Y-%m-%d %H:%M")
    if delta_s < 60:
        return "now"
    if delta_s < 3600:
        return f"{int(delta_s // 60)}m ago"
    if delta_s < 86_400:
        return f"{int(delta_s // 3600)}h ago"
    if delta_s < 86_400 * 7:
        return f"{int(delta_s // 86_400)}d ago"
    return value.strftime("%Y-%m-%d")


def _parse_dt(value):
    from datetime import datetime
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
