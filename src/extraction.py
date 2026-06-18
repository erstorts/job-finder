"""LLM extraction — the ONLY LLM touchpoint in JAMS (PRD §4, T1/T2, REPEAT2).

The provider call sits behind a thin interface here so it can be swapped later.
Default provider is Anthropic Claude via the official ``anthropic`` SDK, using
**structured output via tool use** (PRD §4): the schema is offered as a forced
tool and the returned tool input is validated with Pydantic (X1). Tool use is
used rather than the strict ``messages.parse`` JSON-schema mode because the
extraction contract has many optional fields and the strict compiler rejects it
("Schema is too complex"); tool-input validation happens client-side in Pydantic
instead, which is exactly what X1 requires.

The LLM is called exactly once per job, at capture, for extraction only. It
never scores or decides (DECISIONS.md #3), and the result is frozen by the
caller to ``job.extracted`` and never regenerated (REPEAT2). The API key is read
from ``ANTHROPIC_API_KEY`` by the SDK; it is never hard-coded (TECH8).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, ValidationError

from src.models import AliasSuggestions, JobExtraction

# .env lives at the project root, one level above this src/ package.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file() -> None:
    """Load ``.env`` from the project root into ``os.environ`` (idempotent).

    Lets the app pick up ``ANTHROPIC_API_KEY`` from ``.env`` without the user
    having to ``export`` it before every ``streamlit run`` (TECH8: the key is
    still read from the environment, never hard-coded). Implemented with the
    standard library only so no new dependency is added (TECH6). An existing
    environment variable always wins, so a real ``export`` still overrides the
    file; the SDK reads the value at client construction either way.
    """
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

# Instruction shared by both extraction calls: extract only what is present,
# never guess. This is the heart of T2 / X1 — a hallucinated salary or skill
# would silently corrupt the frozen facts that scoring depends on.
_EXTRACTION_SYSTEM = (
    "You extract structured facts from a single job listing. Populate ONLY "
    "fields explicitly supported by the listing text. If a field is not present, "
    "leave it null (or an empty list). Never guess, infer, or invent values. "
    "Normalize the seniority field to one of: intern, junior, mid, senior, "
    "staff, lead, manager."
)

_ALIAS_SYSTEM = (
    "You propose alternative surface forms (aliases) for canonical skills, to "
    "build a controlled matching vocabulary. For each canonical skill, suggest "
    "common tools, synonyms, and abbreviations a job listing might use. Return "
    "only plausible, well-known aliases."
)


class SupportsMessages(Protocol):
    """Minimal interface JAMS needs from an LLM client.

    Mirrors ``anthropic.Anthropic().messages.create``. Declaring it as a Protocol
    lets tests inject a fake client and lets the provider be swapped without
    touching callers.
    """

    @property
    def messages(self) -> Any: ...


def _default_client() -> Any:
    """Construct the Anthropic client lazily.

    Imported inside the function so importing this module (e.g. in tests) does
    not require the ``anthropic`` package or a configured API key.
    """
    import anthropic

    _load_env_file()
    return anthropic.Anthropic()


def _tool_for(model: type[BaseModel], name: str, description: str) -> dict[str, Any]:
    """Build a tool definition whose input_schema is the Pydantic model's schema.

    Plain (non-strict) tool use, so the schema is guidance the model fills in —
    not the strict JSON-schema compiler that rejects complex contracts. The
    result is validated client-side by Pydantic (X1).
    """
    return {
        "name": name,
        "description": description,
        "input_schema": model.model_json_schema(),
    }


def _forced_tool_call(
    client: SupportsMessages,
    config: Mapping[str, Any],
    *,
    system: str,
    user_content: str,
    tool: dict[str, Any],
) -> dict[str, Any] | None:
    """Run a tool-forced message and return the tool input dict, or ``None``.

    Returns ``None`` if the model produced no matching tool_use block (e.g. a
    refusal), so callers can decide whether that is fatal.
    """
    llm_cfg = config["llm"]
    response = client.messages.create(
        model=llm_cfg["model"],
        max_tokens=llm_cfg["max_tokens"],
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
            return block.input
    return None


def extract_job(
    jd_text: str,
    config: Mapping[str, Any],
    *,
    client: SupportsMessages | None = None,
) -> JobExtraction:
    """Extract a :class:`JobExtraction` from pasted listing text (T1, T2, X1).

    Parameters
    ----------
    jd_text:
        The pasted listing text — the input of record. The URL is never fetched
        (T1, N3).
    config:
        Parsed config; ``config["llm"]`` supplies ``model`` and ``max_tokens``.
    client:
        An object exposing ``messages.create`` (the Anthropic client by default).
        Injectable for tests.

    Returns
    -------
    JobExtraction
        The Pydantic-validated extraction.

    Raises
    ------
    ValueError
        If the model returns no tool call (e.g. a refusal) or the returned
        arguments fail Pydantic validation. The caller must not persist a
        partial job (X1).
    """
    if not jd_text or not jd_text.strip():
        raise ValueError("Cannot extract from empty listing text.")

    client = client or _default_client()
    tool = _tool_for(
        JobExtraction,
        "record_job_extraction",
        "Record the structured facts extracted from the job listing.",
    )
    raw = _forced_tool_call(
        client, config, system=_EXTRACTION_SYSTEM, user_content=jd_text, tool=tool
    )
    if raw is None:
        raise ValueError(
            "LLM returned no structured extraction (possible refusal or "
            "truncation). Not persisting a partial job."
        )
    try:
        return JobExtraction.model_validate(raw)  # X1: validate before persisting
    except ValidationError as exc:
        raise ValueError(f"Extraction failed schema validation: {exc}") from exc


def suggest_aliases(
    canonical_skills: list[str],
    config: Mapping[str, Any],
    *,
    client: SupportsMessages | None = None,
) -> AliasSuggestions:
    """Propose aliases for the user's canonical skills (S4, reviewed step).

    The returned suggestions are shown to the user for confirmation; nothing is
    written to ``skill_alias`` here. Returns an empty wrapper rather than raising
    when the model produces nothing, since this is an optional convenience.
    """
    if not canonical_skills:
        return AliasSuggestions()

    client = client or _default_client()
    tool = _tool_for(
        AliasSuggestions,
        "record_alias_suggestions",
        "Record proposed aliases for the user's canonical skills.",
    )
    prompt = (
        "Propose aliases for these canonical skills:\n"
        + "\n".join(f"- {s}" for s in canonical_skills)
    )
    raw = _forced_tool_call(
        client, config, system=_ALIAS_SYSTEM, user_content=prompt, tool=tool
    )
    if raw is None:
        return AliasSuggestions()
    try:
        return AliasSuggestions.model_validate(raw)
    except ValidationError:
        return AliasSuggestions()
