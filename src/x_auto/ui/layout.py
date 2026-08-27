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
from ..config import Settings
from ..store.repos import Database

MODEL_OPTIONS = [
    "MiniMax-M3",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
]


def render_sidebar(
    settings: Settings,
    db: Database,
) -> None:
    with st.sidebar:
        st.markdown("### X-Automation")
        _render_model_picker(settings)
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
        key="sidebar_model",
        label_visibility="collapsed",
        help=(
            "MiniMax-M3: 1M context, multimodal. "
            "M2.7: 200k context, text-only. "
            "M2.7-highspeed: faster, same price."
        ),
    )
    # Auto-save on change. ``on_change`` fires whenever the user picks a
    # different value, so no Save button is needed. We pass the chosen
    # value via a closure (Streamlit's on_change signature takes no
    # arguments from the caller — it has to read from session state).
    if chosen != current:
        try:
            _write_model_choice(settings, chosen)
            st.toast(f"Model set to {chosen}", icon="✅")
        except Exception as exc:  # noqa: BLE001  — surface to user
            st.toast(f"Couldn't save model: {exc}", icon="⚠️")


def _write_model_choice(settings: Settings, model_id: str) -> None:
    path = settings.config_dir / "settings.yaml"
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return
    mm = data.setdefault("minimax", {})
    if not isinstance(mm, dict):
        return
    mm["model_id"] = model_id
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _render_projects_editor(settings: Settings, db: Database) -> None:
    st.markdown("## Projects")
    st.caption(
        "The Create tab auto-picks the best project from this list for "
        "each generated reply. Description / tags columns are stored in "
        "the DB but not yet used by the AI."
    )

    editor_key = "sidebar_projects_editor"
    if editor_key not in st.session_state:
        st.session_state[editor_key] = _projects_to_rows(
            load_csv(csv_path(settings))
        )

    # Save / Revert pinned above the table so the user can find them
    # even when the table scrolls off-screen.
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save", key="sidebar_csv_save", use_container_width=True):
            _save_projects(settings, db, editor_key)
    with c2:
        if st.button("Revert", key="sidebar_csv_revert", use_container_width=True):
            st.session_state[editor_key] = _projects_to_rows(
                load_csv(csv_path(settings))
            )
            st.rerun()

    st.caption(
        "Rows: project name + the URL the AI should insert as a CTA "
        "(`http://` or `https://`). Save commits the table to "
        "`data/projects.csv`; Revert reloads the last saved version."
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
    st.session_state[editor_key] = _projects_to_rows(projects)
    st.toast(f"Saved {len(projects)} project(s)", icon="✅")


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
