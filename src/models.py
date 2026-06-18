"""Pydantic schemas for JAMS — the LLM extraction contract (PRD §8).

The LLM's only job is to populate :class:`JobExtraction` from pasted listing
text (DECISIONS.md #3: parser, not judge). Fields not present in the text must
be ``null`` (or an empty list), never invented. The validated object is frozen
to ``job.extracted`` at capture and all later scoring reads from it (X2).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JobExtraction(BaseModel):
    """Structured facts extracted from a single job listing (PRD §8).

    Every field maps 1:1 to the contract in the PRD. Optional scalars are
    ``None`` when absent; list fields default to empty. ``min_years`` is
    captured but deliberately not scored in v1 (§9.2).
    """

    company_name: str | None = None
    title: str | None = None
    location: str | None = None
    remote_flag: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    benefits: str | None = None
    company_description: str | None = None
    company_types: list[str] = Field(default_factory=list)   # e.g. ["saas", "startup"]; [] if unclear
    required_skills: list[str] = Field(default_factory=list)  # skills the listing marks as required
    preferred_skills: list[str] = Field(default_factory=list)  # nice-to-have skills
    min_years: int | None = None             # captured but NOT scored in v1
    degree_required: bool | None = None
    seniority: str | None = None             # ordinal band, same vocabulary as profile
    hard_constraints: list[str] = Field(default_factory=list)  # e.g. ["security clearance", "on-site NYC"]


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
