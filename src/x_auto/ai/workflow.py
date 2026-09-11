"""4-step draft workflow.

Linear pipeline:

  1. Understand   — extract any URL from the source tweet text and capture
                     a topic hint. Deterministic; no LLM call.
  2. Rephrase     — LLM call #1. Given the source + the user's extra
                     instructions, write a fresh take on the source's idea
                     in the user's voice. Returns the main tweet body
                     and a one-line topic summary.
  3. Match + CTA  — LLM call #2. Given the source, the topic, and the
                     user's full project list, pick the best-fit project
                     and write a fresh CTA (a short call-to-action +
                     the chosen project's URL) that doesn't copy the
                     source's wording.
  4. Fill         — deterministic. Validate the AI's project pick
                     against the list, fall back to the first project
                     if the pick is invalid, guarantee the CTA contains
                     the project's URL, strip any URL that leaked into
                     the main body, and assemble a Draft row.

Why two LLM calls instead of one? The rephrase task and the
match+CTA task are conceptually different: rephrase cares about
voice and freshness; match cares about topic fit. Splitting them
gives each LLM call one job, makes the per-step output easy to
inspect in tests, and makes step-level retries trivial (we can
re-run just the match step if the AI picks an unknown project).

Why a plain Python class instead of LangGraph? For a linear
4-step pipeline with no branching and no persistent cross-call
state, LangGraph is heavier than it needs to be — it adds a
dependency, per-node boilerplate, and one more layer of indirection
when debugging. The 4-step shape here maps to a StateGraph 1:1
though, so swapping in LangGraph later is a thin wrapper if the
workflow ever needs conditional edges (e.g. "if no project matches
with confidence > 0.6, ask the user to pick from the top 3
candidates instead of posting the LLM's choice").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..store.models import Draft, WritingMode
from ..store.performance import load_winners
from ..utils.media_catalog import (
    catalog_path_for_data_dir,
    list_project_media_with_descriptions,
)
from ..utils.media_matching import match_best_media
from ..utils.text import clean_humanized_text, extract_first_url, format_cta_reply
from .client import AIClient
from .prompts import (
    DRAFT_SYSTEM,
    MATCH_SYSTEM,
    ORIGINAL_TAKE_SYSTEM,
    build_match_user,
    build_rephrase_user,
    get_agent_original_take_system,
    get_agent_rephrase_system,
)

# Conservative character caps. The X free plan caps tweets at 280.
MAIN_HARD_CAP = 280
CTA_HARD_CAP = 280

# Per-step required-keys tuples. The rephrase call expects
# {main, topic, reasoning}; the match call expects
# {project_name, cta_text, reasoning}. These flow into
# AIClient.generate_draft(..., required_keys=...) so the per-step
# validation is tight.
REPHRASE_KEYS = ("main", "topic", "reasoning")
MATCH_KEYS = ("project_name", "cta_text", "reasoning")


@dataclass
class WorkflowResult:
    """What ``DraftWorkflow.run`` returns.

    ``draft`` is ready to persist (status="draft"). The other fields
    are surfaced to the UI for transparency ("the AI picked Atlas
    because the source was about DeFi perps") and for the post-fill
    validation messages.
    """

    draft: Draft
    project_name: str
    project_url: str
    cta_text: str
    topic: str
    source_url: str | None
    rephrase_reasoning: str
    match_reasoning: str
    fallback_used: bool = False  # True if the AI's pick was invalid and we fell back
    writing_mode: WritingMode = "rephrase"


@dataclass
class _WorkflowState:
    """Internal mutable state passed between the 4 steps."""

    source_text: str
    source_author: str
    source_tweet_id: str | None
    writing_mode: WritingMode
    projects: list[dict[str, Any]]
    extra_instructions: str = ""
    image_paths: list[str] = field(default_factory=list)

    # Filled in by the steps.
    source_url: str | None = None
    topic: str = ""
    main: str = ""
    cta_text: str = ""
    project_name: str = ""
    project_url: str = ""
    rephrase_reasoning: str = ""
    match_reasoning: str = ""
    fallback_used: bool = False


class DraftWorkflow:
    """Linear 4-step draft workflow. One instance, many runs."""

    def __init__(self, ai: AIClient) -> None:
        self.ai = ai

    def run(
        self,
        *,
        source_text: str,
        source_author: str,
        source_tweet_id: str | None,
        projects: list[dict[str, Any]],
        writing_mode: WritingMode = "rephrase",
        extra_instructions: str = "",
        image_paths: list[str] | None = None,
    ) -> WorkflowResult:
        state = _WorkflowState(
            source_text=source_text,
            source_author=source_author,
            source_tweet_id=source_tweet_id,
            writing_mode=writing_mode,
            projects=list(projects),
            extra_instructions=extra_instructions,
            image_paths=list(image_paths or []),
        )
        self._step_understand(state)
        self._step_rephrase(state)
        self._step_match_and_cta(state)
        self._step_fill(state)
        return self._to_result(state)

    # ---- step 1: understand ---------------------------------------------

    def _step_understand(self, state: _WorkflowState) -> None:
        """Extract any URL the source tweet already points at.

        The source's URL is a topic hint for the match step: if the
        source promotes a DeFi perp protocol, the user's most
        semantically related project is likely their own DeFi perp
        affiliate link. We don't copy the URL — we just pass it
        forward as context.
        """
        state.source_url = extract_first_url(state.source_text) or None

    # ---- step 2: rephrase (LLM call #1) ---------------------------------

    def _step_rephrase(self, state: _WorkflowState) -> None:
        """LLM writes a fresh take on the source's idea."""
        user_msg = build_rephrase_user(
            source_tweet_text=state.source_text,
            source_tweet_author=state.source_author,
            source_url=state.source_url,
            extra_instructions=state.extra_instructions,
            num_images=len(state.image_paths),
        )
        result = self.ai.generate_draft(
            system=(
                ORIGINAL_TAKE_SYSTEM
                if state.writing_mode == "original_take"
                else DRAFT_SYSTEM
            ),
            user=user_msg,
            required_keys=REPHRASE_KEYS,
        )
        state.main = (result.get("main") or "").strip()
        state.topic = (result.get("topic") or "").strip()
        state.rephrase_reasoning = (result.get("reasoning") or "").strip()

        # Defensive: enforce the no-URL-in-main rule even if the LLM
        # leaks one in. This is the cost invariant; we cannot rely on
        # the prompt alone.
        state.main = clean_humanized_text(_strip_url(state.main))
        if len(state.main) > MAIN_HARD_CAP:
            state.main = state.main[:MAIN_HARD_CAP].rsplit(" ", 1)[0]

    # ---- step 3: match + CTA (LLM call #2) ------------------------------

    def _step_match_and_cta(self, state: _WorkflowState) -> None:
        """LLM picks the best project + writes a fresh CTA."""
        if not state.projects:
            # No projects in the CSV — we have nothing to match
            # against. The fill step will leave the reply empty.
            return
        user_msg = build_match_user(
            source_tweet_text=state.source_text,
            source_tweet_author=state.source_author,
            source_url=state.source_url,
            topic=state.topic,
            projects=state.projects,
        )
        result = self.ai.generate_draft(
            system=MATCH_SYSTEM,
            user=user_msg,
            required_keys=MATCH_KEYS,
        )
        state.project_name = (result.get("project_name") or "").strip()
        state.cta_text = (result.get("cta_text") or "").strip()
        state.match_reasoning = (result.get("reasoning") or "").strip()

    # ---- step 4: fill (deterministic) ------------------------------------

    def _step_fill(self, state: _WorkflowState) -> None:
        """Validate the AI's project pick, guarantee URL in CTA, build Draft."""
        if not state.projects:
            return  # nothing to point at; cta_text stays empty

        project = _find_project(state.project_name, state.projects)
        if project is None:
            # The AI either returned an unknown name or no name at all.
            # Fall back to the first project so the user always has a
            # concrete link in the reply (and a non-zero cost preview).
            project = state.projects[0]
            state.fallback_used = True

        state.project_name = project["name"]
        state.project_url = project["url"]
        state.cta_text = format_cta_reply(state.cta_text, state.project_url)

    # ---- assemble result -------------------------------------------------

    @staticmethod
    def _to_result(state: _WorkflowState) -> WorkflowResult:
        return WorkflowResult(
            draft=Draft(
                source_tweet_id=state.source_tweet_id,
                quote_tweet_id=None,
                writing_mode=state.writing_mode,
                body=state.main,
                link_url=state.cta_text or None,
                image_paths=state.image_paths,
                tone="",
                status="draft",
            ),
            project_name=state.project_name,
            project_url=state.project_url,
            cta_text=state.cta_text,
            topic=state.topic,
            source_url=state.source_url,
            rephrase_reasoning=state.rephrase_reasoning,
            match_reasoning=state.match_reasoning,
            fallback_used=state.fallback_used,
            writing_mode=state.writing_mode,
        )


# ---- helpers ----------------------------------------------------------------

def _find_project(
    name: str, projects: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Case-insensitive lookup of a project by name."""
    if not name:
        return None
    needle = name.strip().lower()
    for p in projects:
        if str(p.get("name", "")).strip().lower() == needle:
            return p
    return None


def _strip_url(text: str) -> str:
    """Remove the first URL found in the text. Defensive guard for the
    no-URL-in-main-body cost invariant.
    """
    from ..utils.text import contains_url, extract_first_url

    if not contains_url(text):
        return text
    url = extract_first_url(text) or ""
    cleaned = text.replace(url, "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


# ---- Agent Workflow (A/B Drafts + Tone Enforcement) ------------------------

@dataclass
class AgentWorkflowResult:
    """Output of AgentDraftWorkflow.run()."""

    source_tweet_id: str | None
    source_author: str
    source_text: str
    source_url: str | None
    topic: str
    project_name: str
    project_url: str
    cta_text: str
    match_reasoning: str
    # Option A (Rephrase)
    option_a_main: str
    option_a_reasoning: str
    # Option B (Original Take)
    option_b_main: str
    option_b_reasoning: str
    niche: str = "crypto"
    recommended_media_path: str | None = None
    recommended_media_name: str | None = None


class AgentDraftWorkflow:
    """Autonomous agent draft workflow generating Option A & Option B with niche tone."""

    def __init__(self, ai: AIClient, niche: str = "crypto", data_dir: Path | None = None) -> None:
        self.ai = ai
        self.niche = niche.strip().lower()
        self.data_dir = data_dir

    def run(
        self,
        *,
        source_text: str,
        source_author: str,
        source_tweet_id: str | None,
        projects: list[dict[str, Any]],
        extra_instructions: str = "",
    ) -> AgentWorkflowResult:
        # 1. Understand: extract source URL hint
        source_url = extract_first_url(source_text) or None

        # Load winning post exemplars for self-improving few-shot prompting
        winning_examples: list[str] = []
        if self.data_dir:
            winners = load_winners(self.data_dir, limit=3)
            winning_examples = [w["body"] for w in winners if w.get("body")]

        # 2. Option A: Rephrase with niche tone
        user_msg_a = build_rephrase_user(
            source_tweet_text=source_text,
            source_tweet_author=source_author,
            source_url=source_url,
            extra_instructions=extra_instructions,
            winning_examples=winning_examples or None,
        )
        sys_a = get_agent_rephrase_system(self.niche)
        res_a = self.ai.generate_draft(
            system=sys_a,
            user=user_msg_a,
            required_keys=REPHRASE_KEYS,
        )
        opt_a_main = clean_humanized_text(
            _strip_url((res_a.get("main") or "").strip()),
            niche=self.niche,
        )
        if len(opt_a_main) > MAIN_HARD_CAP:
            opt_a_main = opt_a_main[:MAIN_HARD_CAP].rsplit(" ", 1)[0]
        topic = (res_a.get("topic") or "").strip()
        opt_a_reasoning = (res_a.get("reasoning") or "").strip()

        # 3. Option B: Original take with niche tone
        user_msg_b = build_rephrase_user(
            source_tweet_text=source_text,
            source_tweet_author=source_author,
            source_url=source_url,
            extra_instructions=extra_instructions,
            winning_examples=winning_examples or None,
        )
        sys_b = get_agent_original_take_system(self.niche)
        res_b = self.ai.generate_draft(
            system=sys_b,
            user=user_msg_b,
            required_keys=REPHRASE_KEYS,
        )
        opt_b_main = clean_humanized_text(
            _strip_url((res_b.get("main") or "").strip()),
            niche=self.niche,
        )
        if len(opt_b_main) > MAIN_HARD_CAP:
            opt_b_main = opt_b_main[:MAIN_HARD_CAP].rsplit(" ", 1)[0]
        opt_b_reasoning = (res_b.get("reasoning") or "").strip()

        # 4. Match activation project and create CTA reply
        project_name = ""
        project_url = ""
        cta_text = ""
        match_reasoning = ""
        if projects:
            match_user = build_match_user(
                source_tweet_text=source_text,
                source_tweet_author=source_author,
                source_url=source_url,
                topic=topic or "general",
                projects=projects,
            )
            match_res = self.ai.generate_draft(
                system=MATCH_SYSTEM,
                user=match_user,
                required_keys=MATCH_KEYS,
            )
            raw_proj_name = (match_res.get("project_name") or "").strip()
            raw_cta = (match_res.get("cta_text") or "").strip()
            match_reasoning = (match_res.get("reasoning") or "").strip()

            proj = _find_project(raw_proj_name, projects)
            if proj is None:
                proj = projects[0]
            project_name = proj["name"]
            project_url = proj["url"]
            cta_text = format_cta_reply(raw_cta, project_url, niche=self.niche)

        # 5. Semantic Media Auto-Matching: Match topic against media catalog
        recommended_media_path: str | None = None
        recommended_media_name: str | None = None
        if self.data_dir and project_name:
            catalog_path = catalog_path_for_data_dir(self.data_dir)
            media_files = list_project_media_with_descriptions(
                self.data_dir / "media_cache",
                catalog_path,
                project_name,
            )
            matched = match_best_media(media_files, text=source_text, topic=topic)
            if matched:
                recommended_media_name = matched.get("filename")
                recommended_media_path = matched.get("path")

        return AgentWorkflowResult(
            source_tweet_id=source_tweet_id,
            source_author=source_author,
            source_text=source_text,
            source_url=source_url,
            topic=topic,
            project_name=project_name,
            project_url=project_url,
            cta_text=cta_text,
            match_reasoning=match_reasoning,
            option_a_main=opt_a_main,
            option_a_reasoning=opt_a_reasoning,
            option_b_main=opt_b_main,
            option_b_reasoning=opt_b_reasoning,
            niche=self.niche,
            recommended_media_path=recommended_media_path,
            recommended_media_name=recommended_media_name,
        )

