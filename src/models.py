"""Pydantic schemas for JAMS — the LLM extraction contract (PRD §8).

The LLM's only job is to populate :class:`JobExtraction` from pasted listing
text (DECISIONS.md #3: parser, not judge). Fields not present in the text must
be ``null`` (or an empty list), never invented. The validated object is frozen
to ``job.extracted`` at capture and all later scoring reads from it (X2).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JobExtraction(BaseModel):
    """Structured facts extracted from a single job listing.

    Focused on what the ATS keyword match needs: the identity of the role, the
    pay range, and the required/preferred skills that get matched against the
    resume + LinkedIn text. ``location`` and ``remote_flag`` are captured only as
    hints to prefill the Denver/remote toggle at triage. Optional scalars are
    ``None`` when absent; list fields default to empty. Nothing is invented —
    fields not present in the listing text stay null/empty.
    """

    company_name: str | None = None
    title: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    required_skills: list[str] = Field(default_factory=list)  # skills the listing marks as required
    preferred_skills: list[str] = Field(default_factory=list)  # nice-to-have skills
    # Hints only (the user sets the real Denver/remote flag at triage).
    location: str | None = None
    remote_flag: bool | None = None
    cover_letter_mentioned: bool | None = None  # hint: does the listing mention a cover letter


class AliasSuggestion(BaseModel):
    """One proposed (canonical_skill, alias) pair for the S4 reviewed step.

    Suggestions are only written to ``skill_alias`` after the user confirms; the
    LLM never edits the vocabulary at runtime.
    """

    canonical_skill: str
    alias: str


class AliasSuggestions(BaseModel):
    """Wrapper so the LLM returns a single structured object for S4."""

    suggestions: list[AliasSuggestion] = Field(default_factory=list)
