"""Tests for the extraction wrapper (PRD T2, X1).

The LLM is never called for real here — a fake client is injected. These tests
lock the wrapper's contract: it returns a validated JobExtraction on success and
raises (never persists a partial) on no structured output.
"""

from __future__ import annotations

import pytest

from src import extraction as extraction_mod
from src.config import get_config

CONFIG = get_config()


class _ToolUseBlock:
    """Stand-in for an Anthropic tool_use content block."""

    type = "tool_use"

    def __init__(self, name, input):
        self.name = name
        self.input = input


class _FakeMessages:
    def __init__(self, tool_input):
        self._tool_input = tool_input
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # No tool_input -> simulate a refusal (no tool_use block returned).
        content = []
        if self._tool_input is not None:
            tool_name = kwargs["tool_choice"]["name"]
            content = [_ToolUseBlock(tool_name, self._tool_input)]
        return type("Resp", (), {"content": content})


class _FakeClient:
    def __init__(self, tool_input):
        self.messages = _FakeMessages(tool_input)


def test_extract_returns_validated_object() -> None:
    client = _FakeClient({
        "company_name": "Acme", "title": "Data Engineer",
        "required_skills": ["python"],
    })
    result = extraction_mod.extract_job("Acme is hiring a Data Engineer...",
                                        CONFIG, client=client)
    assert result.company_name == "Acme"
    assert result.required_skills == ["python"]
    # The configured model and a forced tool call are what we asked for.
    call = client.messages.calls[0]
    assert call["model"] == CONFIG["llm"]["model"]
    assert call["tool_choice"] == {"type": "tool", "name": "record_job_extraction"}


def test_extract_empty_text_raises() -> None:
    with pytest.raises(ValueError):
        extraction_mod.extract_job("   ", CONFIG, client=_FakeClient(None))


def test_extract_no_tool_call_raises() -> None:
    # Simulates a refusal: no tool_use block -> must not write a partial job.
    client = _FakeClient(None)
    with pytest.raises(ValueError):
        extraction_mod.extract_job("some listing text", CONFIG, client=client)


def test_extract_invalid_arguments_raise() -> None:
    # Wrong type for an int field must fail Pydantic validation (X1).
    client = _FakeClient({"salary_min": "not-a-number"})
    with pytest.raises(ValueError):
        extraction_mod.extract_job("listing text", CONFIG, client=client)


def test_suggest_aliases_passthrough() -> None:
    client = _FakeClient({
        "suggestions": [{"canonical_skill": "orchestration", "alias": "dagster"}]
    })
    result = extraction_mod.suggest_aliases(["orchestration"], CONFIG, client=client)
    assert result.suggestions[0].alias == "dagster"


def test_suggest_aliases_empty_input_skips_call() -> None:
    client = _FakeClient(None)
    result = extraction_mod.suggest_aliases([], CONFIG, client=client)
    assert result.suggestions == []
    assert client.messages.calls == []  # no LLM call for empty input
