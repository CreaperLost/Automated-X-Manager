"""Sidebar layout: two flat sections — Model and Projects.

The v3 simplification dropped the ``Settings`` expander wrapper and the
manual Save buttons. The model picker writes the choice to
``config/settings.yaml`` as soon as the user picks one; the Projects
editor's Save / Revert pair is pinned above the table so it stays in
view while the table grows.
"""
from __future__ import annotations

import streamlit as st
import yaml

from ..ai.projects import csv_path, load_csv, sync_projects, write_csv
from ..config import Settings, load_accounts, write_accounts
from ..store.repos import Database
from ..utils.media_library import ensure_project_media_dirs

MODEL_OPTIONS = [
    "MiniMax-M3",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
]


NICHE_OPTIONS = ["Crypto", "AI"]
NICHE_DISPLAY_NAMES = {
    "crypto": "Crypto",
    "ai": "AI",
}


def render_sidebar(
    settings: Settings,
    db: Database,
) -> None:
    with st.sidebar:
        st.markdown("### X-Automation")
        current_niche_key = getattr(settings, "niche", "crypto").lower()
        current_niche = NICHE_DISPLAY_NAMES.get(current_niche_key, "Crypto")

        if st.session_state.get("sidebar_niche_switch") not in NICHE_OPTIONS:
            st.session_state["sidebar_niche_switch"] = current_niche

        chosen_niche = st.segmented_control(
            "Niche",
            NICHE_OPTIONS,
            default=current_niche,
            key="sidebar_niche_switch",
            help="Switch between Crypto and AI settings, creators, and activations.",
        ) or current_niche
        if chosen_niche.lower() != current_niche_key:
            st.session_state["active_niche"] = chosen_niche
            st.rerun()

        st.markdown("---")
        _render_model_picker(settings)
        st.markdown("---")
        _render_handles_editor(settings)
        st.markdown("---")
        _render_projects_editor(settings, db)


def _render_model_picker(settings: Settings) -> None:
    st.markdown("## Model")
    current = settings.minimax.model_id
    if current not in MODEL_OPTIONS:
        MODEL_OPTIONS.append(current)
    chosen = st.selectbox(
        "Model",
        MODEL_OPTIONS,
        index=MODEL_OPTIONS.index(current),
        key=f"sidebar_model_{settings.niche}",
        label_visibility="collapsed",
        help=(
            "MiniMax-M3: 1M context, multimodal. "
            "M2.7: 200k context, text-only. "
            "M2.7-highspeed: faster, same price."
        ),
    )
    # Auto-save on change.
    if chosen != current:
        try:
            _write_model_choice(settings, chosen)
            st.toast(f"Model set to {chosen}", icon="✅")
        except Exception as exc:  # noqa: BLE001  — surface to user
            st.toast(f"Couldn't save model: {exc}", icon="⚠️")


def _write_model_choice(settings: Settings, model_id: str) -> None:
    path = settings.config_dir / "settings.yaml"
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        data = {}
    mm = data.setdefault("minimax", {})
    if not isinstance(mm, dict):
        mm = data["minimax"] = {}
    mm["model_id"] = model_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _render_handles_editor(settings: Settings) -> None:
    niche_title = settings.niche.title()
    st.markdown(f"## Creators ({settings.niche.upper()})")
    st.caption(f"Add or remove {niche_title} creator accounts monitored by manual Fetch.")
    editor_key = f"sidebar_creators_{settings.niche}"
    widget_key = editor_key + "_widget"
    if editor_key not in st.session_state:
        st.session_state[editor_key] = load_accounts(settings.config_dir)

    edited = st.data_editor(
        st.session_state[editor_key],
        key=widget_key,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "handle": st.column_config.TextColumn(
                "Creator handle", required=True, help="1–15 letters, numbers, or underscores"
            ),
        },
    )
    invalid = [
        row for row in edited
        if not _valid_handle(str(row.get("handle") or ""))
    ]
    if invalid:
        st.warning(f"{len(invalid)} invalid row(s) will be skipped on save.")

    def revert() -> None:
        st.session_state[editor_key] = load_accounts(settings.config_dir)
        st.session_state.pop(widget_key, None)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save", key=f"sidebar_creators_save_{settings.niche}", use_container_width=True):
            rows = write_accounts(
                settings.config_dir,
                [(row.get("handle") or "") for row in edited],
            )
            st.session_state[editor_key] = rows
            st.toast(f"Saved {len(rows)} creator(s)", icon="✅")
    with c2:
        st.button(
            "Revert",
            key=f"sidebar_creators_revert_{settings.niche}",
            use_container_width=True,
            on_click=revert,
        )


def _valid_handle(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", value.strip()))


def _render_projects_editor(settings: Settings, db: Database) -> None:
    niche_title = settings.niche.title()
    st.markdown(f"## Activations ({settings.niche.upper()})")
    st.caption(
        f"The Create tab auto-picks the best {niche_title} activation from this list for "
        "each generated reply. Description / tags columns are stored in "
        "the DB but not yet used by the AI."
    )

    editor_key = f"sidebar_projects_{settings.niche}"
    if editor_key not in st.session_state:
        st.session_state[editor_key] = _projects_to_rows(
            load_csv(csv_path(settings))
        )

    # Save / Revert pinned above the table so the user can find them
    # even when the table scrolls off-screen.
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save", key=f"sidebar_csv_save_{settings.niche}", use_container_width=True):
            _save_projects(settings, db, editor_key)
    with c2:
        if st.button("Revert", key=f"sidebar_csv_revert_{settings.niche}", use_container_width=True):
            st.session_state[editor_key] = _projects_to_rows(
                load_csv(csv_path(settings))
            )
            st.rerun()

    st.caption(
        "Rows: activation name + the URL the AI should insert as a CTA "
        "(`http://` or `https://`). Save commits the table to disk."
    )
    edited = st.data_editor(
        st.session_state[editor_key],
        key=editor_key + "_widget",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": st.column_config.TextColumn("Name", required=True, width="small"),
            "url": st.column_config.TextColumn("URL", required=True, width="medium"),
        },
    )

    bad = [r for r in edited if not r.get("name") or not r.get("url")]
    if bad:
        st.warning(f"{len(bad)} row(s) missing name or url — skipped on save.")


def _save_projects(settings: Settings, db: Database, editor_key: str) -> None:
    """Save the projects table to disk + sync the DB."""
    projects = _rows_to_projects(st.session_state[editor_key + "_widget"])
    write_csv(csv_path(settings), projects)
    sync_projects(settings, db)
    ensure_project_media_dirs(
        settings.data_dir / "media_cache",
        (project["name"] for project in projects),
    )
    st.session_state[editor_key] = _projects_to_rows(projects)
    st.toast(f"Saved {len(projects)} activation(s)", icon="✅")


def _projects_to_rows(projects: list[dict]) -> list[dict]:
    return [
        {
            "name": p.get("name", ""),
            "url": p.get("url", ""),
        }
        for p in projects
    ]


def _rows_to_projects(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        name = (r.get("name") or "").strip()
        url = (r.get("url") or "").strip()
        if not name or not url:
            continue
        out.append({"name": name, "url": url, "description": "", "tags": []})
    return out
