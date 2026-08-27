"""MiniMax chat client. The endpoint is OpenAI-compatible."""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from ..config import Settings, get_settings


class DraftGenerationError(Exception):
    """Raised when the AI returns malformed output or the call fails."""


class AIClient:
    """Synchronous MiniMax client. One instance per process.

    Uses the official `openai` Python SDK pointed at MiniMax's
    OpenAI-compatible endpoint. JSON mode is enforced via
    `response_format={"type": "json_object"}` and the response is
    re-validated on the way out.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        mm = self._settings.minimax
        if not mm.configured:
            self._client: OpenAI | None = None
        else:
            self._client = OpenAI(base_url=mm.base_url, api_key=mm.api_key)

    @property
    def configured(self) -> bool:
        return self._client is not None

    def generate_draft(
        self,
        *,
        system: str,
        user: str,
        max_retries: int = 2,
        required_keys: tuple[str, ...] = ("main", "reply", "reasoning"),
    ) -> dict[str, Any]:
        """Call the chat API and return the parsed JSON.

        Retries up to ``max_retries`` times total. Failure modes and
        what we do:

        * **Network / 5xx error** — exponential backoff, then retry.
        * **No JSON in the response** (e.g. MiniMax-M3 spent its
          token budget inside a ``<think>…</think>`` block) — follow
          up with a strict "JSON only, no reasoning" prompt. This
          usually gets the model to skip the think block and emit
          just the JSON.
        * **Malformed JSON** — retry with the same prompt; rare with
          ``response_format="json_object"`` but possible if the
          think-stripper trims a brace by accident.

        ``required_keys`` lets the workflow's two LLM calls (rephrase
        expects {main, topic, reasoning}; match expects
        {project_name, cta_text, reasoning}) use one client method
        with different output schemas. The default keeps the legacy
        ``{main, reply, reasoning}`` schema so older callers don't
        need to change.

        Raises ``DraftGenerationError`` on persistent failure.
        """
        if self._client is None:
            raise DraftGenerationError(
                "MINIMAX_API_KEY is not configured. Set it in .env and restart the app."
            )
        mm = self._settings.minimax
        last_err: DraftGenerationError | None = None

        for attempt in range(max_retries + 1):
            messages = self._messages_for_attempt(
                system, user, attempt, last_err
            )
            try:
                resp = self._client.chat.completions.create(
                    model=mm.model_id,
                    response_format={"type": "json_object"},
                    messages=messages,
                    temperature=mm.temperature,
                    max_tokens=mm.max_tokens,
                )
            except Exception as exc:  # openai raises many subclasses
                last_err = DraftGenerationError(f"MiniMax call failed: {exc}")
                if attempt == max_retries:
                    raise last_err from exc
                _sleep(1 + attempt * 2)
                continue

            content = (resp.choices[0].message.content or "").strip()
            try:
                return _parse_and_validate(content, required_keys=required_keys)
            except DraftGenerationError as exc:
                last_err = exc
                if attempt < max_retries:
                    _sleep(1 + attempt)
                    continue
                raise

        raise DraftGenerationError("MiniMax call failed after retries")

    @staticmethod
    def _messages_for_attempt(
        system: str,
        user: str,
        attempt: int,
        last_err: DraftGenerationError | None,
    ) -> list[dict]:
        """Build the chat-completions message list for one attempt.

        On the first attempt we send the original system+user. On a
        retry after a "no JSON" error we add a strict follow-up
        telling the model to skip the think block.
        """
        if attempt == 0 or last_err is None:
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        followup = (
            "Your previous reply had no JSON object. Reply again with "
            "ONLY the JSON object — no chain-of-thought, no "
            "explanation, no markdown fences. The first character of "
            "your reply must be '{'."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "user", "content": followup},
        ]


def _parse_and_validate(
    content: str,
    required_keys: tuple[str, ...] = ("main", "reply", "reasoning"),
) -> dict[str, Any]:
    """Parse the assistant's text as JSON and validate the required keys.

    Robust to two real-world quirks of the MiniMax M-series models:
    1. They emit a leading ``<think>…</think>`` reasoning block even
       when ``response_format={"type": "json_object"}`` is set. We
       strip that block before parsing.
    2. They sometimes surround the JSON with stray prose ("Here is
       the JSON: …"). We locate the first ``{`` and the matching
       closing ``}`` and parse that span only.

    ``required_keys`` defaults to the legacy {main, reply, reasoning}
    schema. The 4-step workflow's match call passes a different tuple
    ({project_name, cta_text, reasoning}).
    """
    if not content:
        raise DraftGenerationError("empty response from MiniMax")

    cleaned = _strip_think_blocks(content)
    json_text = _extract_json_object(cleaned)
    if not json_text:
        raise DraftGenerationError(
            "MiniMax returned no JSON object in its reply"
        )
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise DraftGenerationError(f"MiniMax returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DraftGenerationError(f"MiniMax returned a non-object: {type(data).__name__}")
    for key in required_keys:
        if key not in data:
            raise DraftGenerationError(f"MiniMax JSON missing required key '{key}'")
        if not isinstance(data[key], str):
            raise DraftGenerationError(f"MiniMax JSON '{key}' must be a string")
    return data


def _strip_think_blocks(text: str) -> str:
    """Remove any ``<think>…</think>`` blocks the model inserted."""
    import re

    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_json_object(text: str) -> str:
    """Return the first balanced ``{…}`` substring, or '' if none.

    Tolerant of leading prose / trailing characters — useful when the
    model wraps its JSON in chatty preamble.
    """
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def _sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)
