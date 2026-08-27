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

from dataclasses import dataclass, field
from typing import Any

from ..store.models import Draft
from ..utils.text import extract_first_url
from .client import AIClient
from .prompts import (
    DRAFT_SYSTEM,
    MATCH_SYSTEM,
    build_match_user,
    build_rephrase_user,
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
    are surfaced to the UI for transparency ("the AI picked Ondo
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


@dataclass
class _WorkflowState:
    """Internal mutable state passed between the 4 steps."""

    source_text: str
    source_author: str
    source_tweet_id: str | None
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
        extra_instructions: str = "",
        image_paths: list[str] | None = None,
    ) -> WorkflowResult:
        state = _WorkflowState(
            source_text=source_text,
            source_author=source_author,
            source_tweet_id=source_tweet_id,
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
            system=DRAFT_SYSTEM,
            user=user_msg,
            required_keys=REPHRASE_KEYS,
        )
        state.main = (result.get("main") or "").strip()
        state.topic = (result.get("topic") or "").strip()
        state.rephrase_reasoning = (result.get("reasoning") or "").strip()

        # Defensive: enforce the no-URL-in-main rule even if the LLM
        # leaks one in. This is the cost invariant; we cannot rely on
        # the prompt alone.
        state.main = _strip_url(state.main)
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

        if len(state.cta_text) > CTA_HARD_CAP:
            state.cta_text = state.cta_text[:CTA_HARD_CAP].rsplit(" ", 1)[0]

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

        # Guarantee: the CTA must contain the chosen project's URL.
        # If the AI's CTA doesn't include it, append it.
        if state.project_url and state.project_url not in state.cta_text:
            cta = state.cta_text.rstrip()
            if cta and not cta.endswith((".", "!", "?")):
                cta += "."
            state.cta_text = (cta + " " + state.project_url).strip()

    # ---- assemble result -------------------------------------------------

    @staticmethod
    def _to_result(state: _WorkflowState) -> WorkflowResult:
        return WorkflowResult(
            draft=Draft(
                source_tweet_id=state.source_tweet_id,
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
    return text.replace(url, "").strip()
