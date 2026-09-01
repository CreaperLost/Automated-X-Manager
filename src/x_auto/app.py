"""Streamlit entrypoint for X-Automation.

Run with:
    streamlit run src/x_auto/app.py
    # or: scripts/boot.sh  /  scripts/boot.ps1  (full first-time setup)

UI shape (v3 simplification):
  Sidebar  -> Model picker (auto-save) + Projects editor
  Main     -> 3 session-backed views (Sources, Create, Queue) with no
              global session-cost line. Costs are surfaced per action.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Self-bootstrap: make sure `src/` is on sys.path so `import x_auto`
# works no matter how the app is launched. Streamlit doesn't honor
# `pyproject.toml`'s [tool.pytest.ini_options] pythonpath, so this is
# the only reliable place to set it.
_SRC = Path(__file__).resolve().parent.parent  # .../x-automation/src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st  # noqa: E402  (import after sys.path tweak)

from x_auto.ai.client import AIClient
from x_auto.ai.projects import sync_projects
from x_auto.config import get_settings
from x_auto.store.repos import Database
from x_auto.ui.layout import render_sidebar
from x_auto.ui.tab_create import render as render_create
from x_auto.ui.tab_publish import render as render_publish
from x_auto.ui.tab_sources import render as render_sources
from x_auto.x.auth import TokenManager
from x_auto.x.client import XClient


@st.cache_resource
def _bootstrap() -> tuple:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "state.db")
    sync_projects(settings, db)
    token_manager = TokenManager(settings)
    from x_auto.x.costs import SessionMeter
    meter = SessionMeter()
    x_client = XClient(settings, token_manager=token_manager, meter=meter)
    ai = AIClient(settings)
    return settings, db, x_client, ai, token_manager


def _inject_active_tab_css() -> None:
    """Re-color the active tab indicator to a neutral white.

    The default Streamlit color is a brand red that clashes with the
    coral primary buttons. We use the ARIA ``aria-selected="true"``
    attribute as a stable, theme-agnostic selector.
    """
    st.markdown(
        """
        <style>
          [aria-selected="true"][role="tab"] {
            color: #ffffff !important;
          }
          [aria-selected="true"][role="tab"] p {
            color: #ffffff !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    settings, db, x_client, ai, _token_manager = _bootstrap()
    st.set_page_config(
        page_title=settings.ui.page_title,
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Sidebar: Model picker + Projects editor (no expander wrapper).
    render_sidebar(settings, db)

    # Active-tab color override (once per app run; Streamlit reruns
    # the script on every interaction but the CSS is idempotent).
    _inject_active_tab_css()

    requested_view = st.session_state.pop("requested_view", None)
    if requested_view in ("Sources", "Create", "Queue"):
        st.session_state["navigation_choice"] = requested_view
    if "navigation_choice" not in st.session_state:
        st.session_state["navigation_choice"] = "Sources"
    view = st.segmented_control(
        "Workspace",
        ["Sources", "Create", "Queue"],
        key="navigation_choice",
        label_visibility="collapsed",
    ) or "Sources"

    if view == "Sources":
        render_sources(settings, db, x_client)
    elif view == "Create":
        render_create(settings, db, ai, x_client=x_client)
    else:
        render_publish(settings, db, x_client, ai)


if __name__ == "__main__":
    main()
