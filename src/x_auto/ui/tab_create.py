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
  the cost in Queue's draft card; the editor only
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
* The form's text inputs edit the same draft row automatically.
  **Discard** deletes the row. Queue
  takes over from there: drafts post with one click.
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

import httpx
import streamlit as st

from ..ai.client import AIClient, DraftGenerationError
from ..ai.projects import list_projects
from ..ai.workflow import DraftWorkflow
from ..config import Settings
from ..store.models import Draft, MediaUpload
from ..store.repos import Database
from ..utils.files import (
    ALLOWED_IMAGE_MIMES,
    is_video_path,
    mime_from_extension,
    validate_image,
)
from ..utils.media_library import ensure_project_media_dirs, list_media
from ..utils.text import contains_url, x_char_count
from .tweet_card import render_tweet_preview

# Character thresholds (X allows 280).
SOFT_CHAR_WARN = 260
HARD_CHAR_CAP = 320
MEDIA_FOLDER_COLUMNS = 5
MEDIA_IMAGE_COLUMNS = 6
_UNSORTED_MEDIA = "__unsorted__"


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
    media_project: str | None = None


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
    *,
    x_client=None,
) -> None:
    st.header("Create")
    st.caption(
        "Pick a selected source, choose a writing mode and optional attachment, "
        "then **Generate draft**. The AI writes one draft "
        "in your voice and auto-picks the best project from your CSV for "
        "the reply (a separate post). The editor shows the pick so you can "
        "override it."
    )

    selected = db.list_tweets(status="selected", limit=200)
    projects = list_projects(db)

    # Cross-tab handoff: if the user clicked "Open in Create ↗" in the
    # Queue, the URL carries `?edit_draft=<id>`. Pick it up here
    # and bind the form to that draft so the editor is ready when the
    # user lands on this tab.
    edit_id = st.query_params.get("edit_draft")
    if edit_id and edit_id.lstrip("-").isdigit():
        opened = db.get_draft(int(edit_id))
        _state().editing_draft_id = int(edit_id)
        if opened and opened.source_tweet_id:
            db.set_tweet_status(opened.source_tweet_id, "selected")
            st.session_state["create_selected_source_id"] = opened.source_tweet_id
        if opened:
            st.session_state["create_writing_mode"] = (
                "Original take" if opened.writing_mode == "original_take" else "Rephrase"
            )
        st.query_params.pop("edit_draft", None)
        st.rerun()

    if not selected:
        st.info(
            "No selected sources yet. Open **Sources** and select one — "
            "this step needs at least one to generate from."
        )
        _render_saved_drafts(db, settings, kind="orphaned")
        return

    if not projects:
        st.error(
            "No projects in `data/projects.csv` — add at least one in the "
            "sidebar's **Projects** section. Generate needs a project list to "
            "pick from."
        )
        _render_saved_drafts(db, settings, kind="resumable")
        return

    # ---- 1. Form: source / image / extras (single column) -----------------
    st.markdown("**Source tweet**")
    requested_id = st.session_state.pop("create_selected_source_id", None)
    source_ids = [t.id for t in selected]
    if requested_id in source_ids:
        st.session_state["create_source_id"] = requested_id
    by_id = {t.id: t for t in selected}
    source_id = st.selectbox(
        "Source tweet",
        source_ids,
        format_func=(
            lambda tid: f"@{by_id[tid].account_handle}: {by_id[tid].text[:80]}"
            f"{'…' if len(by_id[tid].text) > 80 else ''}"
        ),
        key="create_source_id",
        label_visibility="collapsed",
        help=(
            f"{len(projects)} project(s) in your CSV — the AI will auto-pick "
            "the best fit for this source. The source's own URL, if any, is "
            "read as a topic hint (it is not copied into your tweet)."
        ),
    )
    source = by_id[source_id]

    writing_label = st.radio(
        "Writing mode",
        ["Rephrase", "Original take"],
        horizontal=True,
        key="create_writing_mode",
        help="Rephrase preserves the core idea. Original take develops a new angle.",
    )
    writing_mode = "original_take" if writing_label == "Original take" else "rephrase"

    attachment_options = ["None"]
    if source.source_image_url:
        attachment_options.append("Use source image")
    attachment_options.append("Upload/use my media")
    attachment = st.radio(
        "Attachment",
        attachment_options,
        horizontal=True,
        key=f"create_attachment_{source.id}",
    )
    upload = None
    if attachment == "Use source image" and source.source_image_url:
        st.image(source.source_image_url, width=320)
    elif attachment == "Upload/use my media":
        media_dir = _render_image_library(db, settings, projects)
        if media_dir is not None:
            upload_kind = st.radio(
                "Upload type",
                ["Image", "Video"],
                horizontal=True,
                key=f"create_media_kind_{_state().media_project}",
                help="Choose Video to upload an MP4, MOV, or WebM file.",
            )
            if upload_kind == "Video":
                upload_types = ["mp4", "mov", "webm"]
            else:
                upload_types = ["jpg", "jpeg", "png", "gif", "webp", "avif"]
            upload = st.file_uploader(
                f"Upload {upload_kind.lower()} to {_state().media_project}",
                type=upload_types,
                accept_multiple_files=False,
                key=f"create_{upload_kind.lower()}_{_state().media_project}",
                help=f"The file will be saved in `{media_dir.relative_to(settings.data_dir)}`.",
            )

    st.markdown("**Extra instructions (optional)**")
    extra = st.text_area(
        "Extra instructions",
        key="create_extra",
        label_visibility="collapsed",
        height=80,
        placeholder="e.g. mention my open-source project, target devs",
    )

    generate_label = "Regenerate draft" if _state().editing_draft_id else "Generate draft"
    if st.button(
        generate_label,
        type="primary",
        key="create_generate",
        use_container_width=True,
        help="Two LLM calls: rephrase the source, then pick a project + write the CTA.",
    ):
        _on_generate(
            settings, db, ai, source, projects, upload, extra or "",
            attachment=attachment,
            writing_mode=writing_mode,
        )

    # ---- 2. Editor + live preview (bound to a draft row in the DB) ------
    editing_id = _state().editing_draft_id
    draft = db.get_draft(editing_id) if editing_id else None
    if draft is None:
        _render_saved_drafts(db, settings, kind="resumable")
        return

    last_result = _state().last_workflow
    _render_editor_with_preview(
        settings, db, draft, last_workflow=last_result,
        x_client=x_client,
    )

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
    *,
    attachment: str = "None",
    writing_mode: str = "rephrase",
) -> None:
    if not ai.configured:
        st.error(
            "MiniMax is not configured. Set `MINIMAX_API_KEY` in `.env` "
            "and restart."
        )
        return

    # Resolve and validate the selected attachment before spending an AI call.
    state = _state()
    image_paths: list[str] = []
    if attachment == "Use source image":
        try:
            image_paths = [_cache_source_image(settings, db, source)]
        except ValueError as exc:
            st.error(f"Couldn't use the source image: {exc}")
            return
    elif attachment == "Upload/use my media":
        picked_path = state.picked_image_path
        if picked_path and Path(picked_path).exists():
            image_paths = [picked_path]
        elif upload is not None:
            media_dirs = ensure_project_media_dirs(
                settings.data_dir / "media_cache",
                (project["name"] for project in projects),
            )
            upload_dir = media_dirs.get(state.media_project or "")
            if upload_dir is None:
                st.error("Choose a project media folder before uploading media.")
                return
            image_paths = _save_upload(upload, upload_dir)
            if not image_paths:
                st.error("Failed to save the media upload.")
                return
    if image_paths:
        st.caption(f"Media attached: `{Path(image_paths[0]).name}`")

    # Run the 4-step workflow: understand → rephrase → match+CTA → fill.
    # Two LLM calls (rephrase + match), each focused on one job.
    workflow = DraftWorkflow(ai)
    with st.spinner("Asking MiniMax (rephrase → pick project → write CTA)…"):
        try:
            wf_result = workflow.run(
                source_text=source.text,
                source_author=source.account_handle,
                source_tweet_id=source.id,
                writing_mode=writing_mode,
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
    x_client=None,
) -> None:
    """Edit the draft and see a live preview.

    Layout (top to bottom, single column):
      1. Source-tweet summary + auto-selected project chip
      2. **Live preview card** — reflects current edits, always visible
      3. Main + reply text inputs
      4. Character + URL warnings (no cost preview — that lives on
         Queue's draft card, where the user actually pays)
      5. Auto-save status / Discard button

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
            "Add a project in the sidebar's **Projects** section, or paste "
            "a URL here manually. The reply "
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

    # Persist edits as soon as Streamlit has received them. This makes
    # adding instructions or adjusting the generated copy safe across
    # tab switches and browser refreshes; the user no longer needs to
    # find and click a separate Save button.
    normalized_reply = (reply_text or "").strip() or None
    if main_text.strip() != draft.body or normalized_reply != draft.link_url:
        draft.body = main_text.strip()
        draft.link_url = normalized_reply
        db.update_draft(draft)
        st.caption("✓ Changes saved automatically")
    else:
        st.caption("Changes save automatically as you edit.")

    if x_client is not None:
        st.markdown("**Publish**")
        if st.button(
            "Post now", key=f"create_post_{draft.id}",
            type="primary", use_container_width=True,
        ):
            # Reuse Queue's deterministic preflight and result banner.
            from .tab_publish import _post_now

            latest = db.get_draft(draft.id) or draft
            _post_now(settings, db, x_client, latest)
            st.session_state["requested_view"] = "Queue"
            st.rerun()

    if st.button(
        "Discard",
        key=f"create_discard_{draft.id}",
        use_container_width=True,
    ):
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


def _cache_source_image(settings: Settings, db: Database, source) -> str:
    """Download the fetched source's first photo into the normal media cache."""
    if not source.source_image_url:
        raise ValueError("this source has no reusable photo")
    try:
        response = httpx.get(source.source_image_url, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"download failed ({exc})") from exc
    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if mime not in ALLOWED_IMAGE_MIMES:
        raise ValueError(f"unsupported image type {mime or 'unknown'}")
    ext = {
        "image/jpeg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/webp": ".webp", "image/avif": ".avif",
    }[mime]
    cache_dir = settings.data_dir / "media_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"source-{source.id}-{uuid.uuid4().hex[:8]}{ext}"
    dest.write_bytes(response.content)
    validation = validate_image(dest)
    if not validation.ok:
        try:
            dest.unlink()
        except OSError:
            pass
        raise ValueError(validation.reason)
    db.register_media_upload(MediaUpload(
        local_path=str(dest), filename=dest.name, mime=mime,
        size=validation.size,
    ))
    return str(dest)


def _render_image_library(
    db: Database,
    settings: Settings,
    projects: list[dict],
) -> Path | None:
    """Render project folders first, then a six-column image picker."""
    cache_dir = settings.data_dir / "media_cache"
    project_dirs = ensure_project_media_dirs(
        cache_dir,
        (project["name"] for project in projects),
    )
    unsorted_images = list_media(cache_dir)
    folders: list[tuple[str, Path, bool]] = [
        (name, folder, True) for name, folder in project_dirs.items()
    ]
    if unsorted_images:
        folders.append(("Unsorted", cache_dir, False))

    state = _state()
    valid_selection_keys = set(project_dirs) | (
        {_UNSORTED_MEDIA} if unsorted_images else set()
    )
    if state.media_project not in valid_selection_keys:
        state.media_project = None

    image_counts = {str(folder): len(list_media(folder)) for _, folder, _ in folders}
    total_images = sum(image_counts.values())
    with st.expander(
        f"📁 Project media · {len(project_dirs)} folders · {total_images} files",
        expanded=True,
    ):
        if state.media_project is None:
            st.caption(
                "Choose a project folder. Images stay grouped by project; "
                f"previews open {MEDIA_IMAGE_COLUMNS} per row."
            )
            for start in range(0, len(folders), MEDIA_FOLDER_COLUMNS):
                columns = st.columns(MEDIA_FOLDER_COLUMNS)
                for offset, (name, folder, is_project) in enumerate(
                    folders[start:start + MEDIA_FOLDER_COLUMNS]
                ):
                    with columns[offset]:
                        count = image_counts[str(folder)]
                        if st.button(
                            f"📁 {name}\n{count} file{'s' if count != 1 else ''}",
                            key=f"media_folder_{start + offset}",
                            use_container_width=True,
                        ):
                            state.media_project = name if is_project else _UNSORTED_MEDIA
                            state.picked_image_path = None
                            st.rerun()
            return None

        if state.media_project == _UNSORTED_MEDIA:
            selected_name = "Unsorted"
            selected_dir = cache_dir
            selected_is_project = False
        else:
            selected_name = state.media_project
            selected_dir = project_dirs[selected_name]
            selected_is_project = True

        back_col, title_col = st.columns([1, 5])
        with back_col:
            if st.button("← Folders", key="media_folder_back", use_container_width=True):
                state.media_project = None
                state.picked_image_path = None
                st.rerun()
        with title_col:
            st.markdown(f"**📂 {selected_name}**")

        rows = _media_rows_for_folder(db, selected_dir)
        if rows:
            picked = state.picked_image_path
            for start in range(0, len(rows), MEDIA_IMAGE_COLUMNS):
                columns = st.columns(MEDIA_IMAGE_COLUMNS)
                for offset, row in enumerate(rows[start:start + MEDIA_IMAGE_COLUMNS]):
                    with columns[offset]:
                        _render_library_card(row, picked == row.local_path)
        else:
            st.info("This folder has no images or videos yet.")

        if selected_is_project:
            relative = selected_dir.relative_to(settings.data_dir)
            st.caption(f"New images and videos will be saved to `{relative}`.")
            return selected_dir

        st.caption(
            "These are older media files stored at the media-cache root. "
            "Choose a project folder to add new media."
        )
        return None


def _media_rows_for_folder(db: Database, folder: Path) -> list[MediaUpload]:
    """Merge files on disk with cached X upload metadata for one folder."""
    db_rows = db.list_media_uploads(limit=1000)
    by_path = {
        str(Path(row.local_path).resolve()): row
        for row in db_rows
    }
    rows: list[MediaUpload] = []
    for path in list_media(folder):
        resolved = str(path.resolve())
        rows.append(by_path.get(resolved) or _local_row_to_upload(resolved))
    return rows


def _local_row_to_upload(local_path: str) -> MediaUpload:
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
    path = Path(row.local_path)
    try:
        if path.exists() and is_video_path(path):
            st.video(str(path))
        elif path.exists() and path.stat().st_size < 5 * 1024 * 1024:
            st.image(str(path), width="stretch")
        else:
            st.caption("(file missing or too large)")
    except OSError:
        st.caption("(file missing)")

    label = f"{row.filename}" + ("  ✓" if is_picked else "")
    st.caption(label)
    if row.size:
        st.caption(f"{row.size // 1024} KB")

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
