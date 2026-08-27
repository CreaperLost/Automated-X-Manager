"""Tab 3 — Create. v6 with the single-column form + namespaced state.

v6 changes
----------
* **Single-column form** — Source → Image → Extra instructions →
  Generate. The earlier 2-col layout put static info on the right
  that just competed with the inputs. Right-column info now lives
  in ``help=`` tooltips on the relevant widgets.
* **Session-state is namespaced** under ``st.session_state["create_state"]``
  via the :class:`CreateState` dataclass. The widget-bound keys
  (``create_source``, ``create_image``, ``create_extra``, etc.)
  stay at the top level because Streamlit requires them there for
  widget state; only the three "app state" keys move into the
  namespace.
* **Cost preview removed** from the editor. The user already sees
  the cost in the Publish tab's draft card; the editor only
  surfaces the ``contains_url`` guard (a 13.3× cost mistake is the
  one thing worth warning about).
* **Text-area height trimmed** 140 → 100 so the Save / Discard
  buttons reach the first viewport on a typical draft body.

v5 changes (preserved)
----------------------
* Project is auto-selected by the AI — no project dropdown.
* Two LLM calls per draft (rephrase, then match+CTA).
* Drafts are persisted to the DB the moment the workflow finishes
  (status="draft"). Browser refresh / app restart / tab switch
  never loses work; the "Your drafts" list shows every saved draft.

Persistence model
-----------------
* The generated draft is persisted to the DB the moment the
  workflow finishes (status="draft").
* The form's text inputs edit the same draft row. **Save draft**
  updates the row, **Discard** deletes the row. The Publish tab
  takes over from there: drafts post or schedule with one click.
* The namespaced session state (:class:`CreateState`) holds the id
  of the row the form is currently bound to, the last workflow
  result, and the picked image-library path. The data itself
  lives in SQLite.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from ..ai.client import AIClient, DraftGenerationError
from ..ai.projects import list_projects
from ..ai.workflow import DraftWorkflow
from ..config import Settings
from ..store.models import Draft
from ..store.repos import Database
from ..utils.files import mime_from_extension
from ..utils.text import contains_url, x_char_count
from .tweet_card import render_tweet_preview

# Character thresholds (X allows 280).
SOFT_CHAR_WARN = 260
HARD_CHAR_CAP = 320


# ---- Session-state namespace -------------------------------------------------

@dataclass
class CreateState:
    """App state for the Create tab.

    The three keys that previously lived as ad-hoc top-level
    session_state entries are now grouped here. Widget-bound keys
    (``create_source``, ``create_image``, etc.) still live at the
    top level because Streamlit needs them there for widget state.
    """
    editing_draft_id: int | None = None
    last_workflow: Any | None = None
    picked_image_path: str | None = None


_STATE_KEY = "create_state"


def _state() -> CreateState:
    """Return the namespaced CreateState, creating it on first access."""
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = CreateState()
    return st.session_state[_STATE_KEY]


# ---- render -----------------------------------------------------------------

def render(
    settings: Settings,
    db: Database,
    ai: AIClient,
) -> None:
    st.header("Create")
    st.caption(
        "Pick a selected tweet, optionally attach one image, add any extra "
        "instructions, then **Generate draft**. The AI rephrases the source "
        "in your voice and auto-picks the best project from your CSV for "
        "the reply (a separate post). The editor shows the pick so you can "
        "override it."
    )

    selected = db.list_tweets(status="selected", limit=200)
    projects = list_projects(db)

    # Cross-tab handoff: if the user clicked "Open in Create ↗" in the
    # Publish tab, the URL carries `?edit_draft=<id>`. Pick it up here
    # and bind the form to that draft so the editor is ready when the
    # user lands on this tab.
    edit_id = st.query_params.get("edit_draft")
    if edit_id and edit_id.lstrip("-").isdigit():
        _state().editing_draft_id = int(edit_id)
        st.query_params.pop("edit_draft", None)
        st.rerun()

    if not selected:
        st.info(
            "No selected tweets yet. Go to **Review** and promote a few — "
            "this step needs at least one to generate from."
        )
        _render_saved_drafts(db, settings, kind="orphaned")
        return

    if not projects:
        st.error(
            "No projects in `data/projects.csv` — add at least one in the "
            "sidebar's **Settings** panel. Generate needs a project list to "
            "pick from."
        )
        _render_saved_drafts(db, settings, kind="resumable")
        return

    # ---- 1. Form: source / image / extras (single column) -----------------
    st.markdown("**Source tweet**")
    source = st.selectbox(
        "Source tweet",
        selected,
        format_func=(
            lambda t: f"@{t.account_handle}: {t.text[:80]}"
            f"{'…' if len(t.text) > 80 else ''}"
        ),
        key="create_source",
        label_visibility="collapsed",
        help=(
            f"{len(projects)} project(s) in your CSV — the AI will auto-pick "
            "the best fit for this source. The source's own URL, if any, is "
            "read as a topic hint (it is not copied into your tweet)."
        ),
    )

    st.markdown("**Image (optional, max 1)**")
    upload = st.file_uploader(
        "Image",
        type=["jpg", "jpeg", "png", "gif", "webp"],
        accept_multiple_files=False,
        key="create_image",
        label_visibility="collapsed",
    )
    _render_image_library(db, settings)

    st.markdown("**Extra instructions (optional)**")
    extra = st.text_area(
        "Extra instructions",
        key="create_extra",
        label_visibility="collapsed",
        height=80,
        placeholder="e.g. mention my open-source project, target devs",
    )

    if st.button(
        "Generate draft",
        type="primary",
        key="create_generate",
        use_container_width=True,
        help="Two LLM calls: rephrase the source, then pick a project + write the CTA.",
    ):
        _on_generate(settings, db, ai, source, projects, upload, extra or "")

    # ---- 2. Editor + live preview (bound to a draft row in the DB) ------
    editing_id = _state().editing_draft_id
    draft = db.get_draft(editing_id) if editing_id else None
    if draft is None:
        _render_saved_drafts(db, settings, kind="resumable")
        return

    last_result = _state().last_workflow
    _render_editor_with_preview(settings, db, draft, last_workflow=last_result)

    # ---- 3. Saved drafts list (Load / Discard) --------------------------
    _render_saved_drafts(
        db, settings, kind="resumable", current_id=draft.id
    )


def _on_generate(
    settings: Settings,
    db: Database,
    ai: AIClient,
    source,
    projects: list[dict],
    upload,
    extra: str,
) -> None:
    if not ai.configured:
        st.error(
            "MiniMax is not configured. Set `MINIMAX_API_KEY` in `.env` "
            "and restart."
        )
        return

    # Image: library pick > fresh upload > none.
    state = _state()
    picked_path = state.picked_image_path
    if picked_path and Path(picked_path).exists():
        image_paths = [picked_path]
    elif upload is not None:
        image_paths = _save_upload(upload, settings.data_dir / "media_cache")
        if not image_paths:
            st.error("Failed to save the upload.")
            return
    else:
        image_paths = []
    if image_paths:
        st.caption(f"Image attached: `{Path(image_paths[0]).name}`")

    # Run the 4-step workflow: understand → rephrase → match+CTA → fill.
    # Two LLM calls (rephrase + match), each focused on one job.
    workflow = DraftWorkflow(ai)
    with st.spinner("Asking MiniMax (rephrase → pick project → write CTA)…"):
        try:
            wf_result = workflow.run(
                source_text=source.text,
                source_author=source.account_handle,
                source_tweet_id=source.id,
                projects=projects,
                extra_instructions=extra,
                image_paths=image_paths,
            )
        except DraftGenerationError as exc:
            st.error(f"AI failed: {exc}")
            return

    # Persist the draft immediately so it survives a refresh / restart.
    new_id = db.create_draft(wf_result.draft)
    wf_result.draft.id = new_id
    state.editing_draft_id = new_id
    state.last_workflow = wf_result
    state.picked_image_path = None

    if wf_result.fallback_used:
        st.warning(
            f"AI didn't pick a known project from the list — fell back to "
            f"**{wf_result.project_name}**. You can change the reply "
            f"manually below."
        )
    st.success(
        f"Generated draft #{new_id} — auto-selected project: "
        f"**{wf_result.project_name}**."
    )
    st.rerun()


def _render_editor_with_preview(
    settings: Settings,
    db: Database,
    draft: Draft,
    *,
    last_workflow=None,
) -> None:
    """Edit the draft and see a live preview.

    Layout (top to bottom, single column):
      1. Source-tweet summary + auto-selected project chip
      2. **Live preview card** — reflects current edits, always visible
      3. Main + reply text inputs
      4. Character + URL warnings (no cost preview — that lives on
         the Publish tab's draft card, where the user actually pays)
      5. Save / Discard buttons

    The preview is rendered BEFORE the editor so the user sees it
    immediately on any viewport. We read the current widget values
    from ``st.session_state`` (the live edited values) with the draft
    row as the fallback for the first render.

    ``last_workflow`` (a ``WorkflowResult``) is shown when present so
    the user can see which project the AI picked and why. It's
    session-state-only and never persisted; older drafts opened
    later won't have it.
    """
    st.markdown("---")
    st.markdown(f"### Editing draft **#{draft.id}**")
    if draft.source_tweet_id:
        src = db.get_tweet(draft.source_tweet_id)
        if src:
            st.caption(
                f"Rephrasing **@{src.account_handle}**: "
                f"{src.text[:120]}{'…' if len(src.text) > 120 else ''}"
            )

    # Auto-selected project chip (transparency). Shown only when we
    # have a workflow result for this draft. The user can still
    # override the reply field by typing a different URL/CTA.
    if last_workflow is not None and last_workflow.project_name:
        chip = (
            f"✓ AI auto-selected: **{last_workflow.project_name}** → "
            f"`{last_workflow.project_url}`"
        )
        if last_workflow.topic:
            chip += f"  \n_Topic: {last_workflow.topic}_"
        if last_workflow.match_reasoning:
            chip += f"  \n_Why: {last_workflow.match_reasoning}_"
        st.success(chip)

    main_key = f"create_main_edit_{draft.id}"
    reply_key = f"create_reply_edit_{draft.id}"

    # Current values: session state (live edits) > draft row (initial).
    main_now = st.session_state.get(main_key, draft.body)
    reply_now = st.session_state.get(reply_key, draft.link_url or "")

    # ---- 1. Preview at the top, always visible -----------------------
    render_tweet_preview(
        main_now,
        reply=reply_now or None,
        image_paths=draft.image_paths,
        display_name="You",
        handle="you",
        posted_at=datetime.now(),
    )

    st.markdown("---")

    # ---- 2. Editor (full width) --------------------------------------
    main_text = st.text_area(
        "Main tweet (≤ 280 chars; no URLs)",
        value=draft.body,
        key=main_key,
        height=100,
    )
    reply_text = st.text_input(
        "Reply tweet (CTA + your project link — sent as a separate post)",
        value=draft.link_url or "",
        key=reply_key,
    )

    if not (reply_text or "").strip():
        st.warning(
            "Reply is empty — the AI's auto-pick didn't match any project. "
            "Add a project to `data/projects.csv` in the sidebar's "
            "**Settings** panel, or paste a URL here manually. The reply "
            "carries your project link in a separate post (saves $0.170 vs "
            "an inline URL)."
        )

    cc = x_char_count(main_text)
    if cc > HARD_CHAR_CAP:
        st.error(f"Main tweet is {cc} characters (X allows 280; please trim).")
    elif cc > SOFT_CHAR_WARN:
        st.warning(f"Main tweet is {cc} characters (X allows 280).")

    if contains_url(main_text):
        st.error(
            "Main tweet contains a URL — would cost $0.200 instead of $0.015. "
            "Move the URL to the reply field below."
        )

    # Cost preview was removed in v3 — the user sees the cost on the
    # Publish tab's draft card (where the action is taken). The
    # contains_url guard above is the one cost mistake worth
    # preventing in the editor (13.3× vs inline URL).

    col_save, col_discard = st.columns(2)
    with col_save:
        if st.button(
            "Save draft",
            key=f"create_save_draft_{draft.id}",
            type="primary",
            use_container_width=True,
        ):
            _update_draft_body(db, draft, main_text, reply_text)
            st.success(
                f"Draft #{draft.id} saved. Open **Publish** to post it."
            )
    with col_discard:
        if st.button("Discard", key=f"create_discard_{draft.id}", use_container_width=True):
            db.delete_draft(draft.id)
            _state().editing_draft_id = None
            st.warning(f"Draft #{draft.id} deleted.")
            st.rerun()


def _update_draft_body(
    db: Database,
    draft: Draft,
    main_text: str,
    reply_text: str,
) -> None:
    draft.body = main_text.strip()
    draft.link_url = (reply_text or "").strip() or None
    db.update_draft(draft)


def _save_upload(upload, cache_dir: Path) -> list[str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(upload.name).suffix.lower() or ".bin"
    dest = cache_dir / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(upload.getbuffer())
    return [str(dest)]


def _render_image_library(db: Database, settings: Settings) -> None:
    """Inline "Pick from library" picker under the file uploader."""
    from ..store.models import MEDIA_ID_TTL_SECONDS

    cache_dir = settings.data_dir / "media_cache"
    rows = db.list_media_uploads(limit=50)

    on_disk: set[str] = set()
    if cache_dir.exists():
        for p in cache_dir.iterdir():
            if p.is_file():
                on_disk.add(str(p.resolve()))
    db_paths: set[str] = {r.local_path for r in rows}
    for path in on_disk - db_paths:
        rows.append(_local_row_to_upload(path, cache_dir))

    if not rows:
        st.caption("No images in library yet. Upload one above to start.")
        return

    with st.expander(f"📷 Image library ({len(rows)})", expanded=False):
        st.caption(
            "Re-use an image you've already uploaded. The publish "
            "flow reuses the cached X `media_id` if it's still valid "
            f"(< {MEDIA_ID_TTL_SECONDS // 3600}h old); otherwise it "
            "re-uploads automatically."
        )
        picked = _state().picked_image_path
        for i in range(0, len(rows), 3):
            cols = st.columns(3)
            for col, row in zip(cols, rows[i:i + 3], strict=False):
                with col:
                    _render_library_card(row, picked == row.local_path)


def _local_row_to_upload(local_path: str, cache_dir: Path):
    from ..store.models import MediaUpload

    p = Path(local_path)
    try:
        size = p.stat().st_size
    except OSError:
        size = None
    return MediaUpload(
        local_path=local_path,
        filename=p.name,
        x_media_id=None,
        x_media_id_uploaded_at=None,
        mime=mime_from_extension(p.name),
        size=size,
    )


def _render_library_card(row, is_picked: bool) -> None:
    from datetime import datetime

    from ..store.models import MEDIA_ID_TTL_SECONDS

    path = Path(row.local_path)
    label = f"**{row.filename}**" + ("  ✓" if is_picked else "")
    st.markdown(label)

    try:
        if path.exists() and path.stat().st_size < 5 * 1024 * 1024:
            st.image(str(path), use_container_width=True)
        else:
            st.caption("(file missing or too large)")
    except OSError:
        st.caption("(file missing)")

    if row.size:
        st.caption(f"{row.size // 1024} KB")
    if row.x_media_id and row.x_media_id_uploaded_at:
        age_s = (datetime.now() - row.x_media_id_uploaded_at).total_seconds()
        if age_s < MEDIA_ID_TTL_SECONDS:
            hours_left = (MEDIA_ID_TTL_SECONDS - age_s) / 3600
            st.caption(
                f"✅ Cached — valid for {hours_left:0.1f}h "
                f"(`{row.x_media_id}`)"
            )
        else:
            st.caption(
                f"⚠️ Cached id expired ({age_s / 3600:0.1f}h old) — "
                "will re-upload on next post"
            )
    else:
        st.caption("Not yet uploaded to X")

    btn_label = "✓ Using this" if is_picked else "Use this"
    if st.button(btn_label, key=f"use_lib_{row.local_path}", use_container_width=True):
        state = _state()
        if is_picked:
            state.picked_image_path = None
        else:
            state.picked_image_path = row.local_path
        st.rerun()


def _render_saved_drafts(
    db: Database,
    settings: Settings,
    *,
    kind: str,
    current_id: int | None = None,
) -> None:
    """List drafts so the user can resume or delete them.

    Two flavours:
    - kind="resumable": drafts that can be opened for editing. Shown
      below the active editor (or in place of one when nothing is open).
    - kind="orphaned": a small note shown only when the tab is in
      "no source tweets" state, so the user can clean up old drafts.
    """
    drafts = db.list_drafts(status="draft", limit=20)
    if not drafts:
        if kind == "resumable":
            st.caption(
                "No saved drafts. Generated drafts land here automatically, "
                "so you can pick up after a refresh."
            )
        return

    if kind == "orphaned":
        st.markdown("---")
        st.markdown("### Saved drafts")
        st.caption(
            "These drafts already exist in the DB. Generate a new source "
            "tweet to resume editing one, or discard to clean up."
        )

    for d in drafts:
        with st.container(border=True):
            label = d.body[:80] + ("…" if len(d.body) > 80 else "")
            st.markdown(
                f"**Draft #{d.id}** — `{label}`"
                + (f" · _reply: {d.link_url}_" if d.link_url else "")
            )
            cols = st.columns(3)
            with cols[0]:
                if st.button(
                    "Load for editing",
                    key=f"create_load_{d.id}",
                    disabled=(d.id == current_id),
                    use_container_width=True,
                ):
                    _state().editing_draft_id = d.id
                    st.rerun()
            with cols[1]:
                if d.image_paths:
                    st.caption(f"🖼 {len(d.image_paths)} image(s)")
            with cols[2]:
                if st.button("Discard", key=f"create_discard_saved_{d.id}", use_container_width=True):
                    db.delete_draft(d.id)
                    if _state().editing_draft_id == d.id:
                        _state().editing_draft_id = None
                    st.rerun()


def _strip_url_if_present(text: str) -> str:
    return (
        text
        .replace("https://", "")
        .replace("http://", "")
        .strip()
    )
