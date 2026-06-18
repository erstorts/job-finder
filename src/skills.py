"""Deterministic skill matching via the controlled alias vocabulary.

A required or preferred skill counts as *matched* if its normalized form maps,
through the ``skill_alias`` table, to a canonical skill the user actually has
(present in their resume/profile). This is a deterministic lookup, NOT embedding
similarity (PRD §9.2, N6). Rationale: a lookup is repeatable and explainable
("matched 7 of 9 required skills, missing Spark and Kubernetes"); a cosine
threshold is neither.

All functions are pure and deterministic (REPEAT1).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


def normalize_skill(text: str | None) -> str:
    """Lowercase, collapse non-alphanumeric runs to single spaces, trim.

    Shared normalization for both alias surface forms and listing skills so the
    lookup compares like with like.
    """
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _padded(text: str) -> str:
    """Space-pad normalized text so substring tests respect word boundaries.

    ``" airflow "`` is in ``" we use airflow daily "`` but not in
    ``" fairflowing "``, giving multi-word aliases token-boundary matching
    without a full tokenizer.
    """
    return f" {normalize_skill(text)} "


def build_alias_index(
    alias_rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Map every normalized surface form to its canonical skill.

    Includes each canonical skill as an alias of itself, so a listing that uses
    the canonical name directly still resolves.

    Parameters
    ----------
    alias_rows:
        Rows with ``canonical_skill`` and ``alias`` keys (from ``skill_alias``).

    Returns
    -------
    dict
        ``{normalized_surface_form: canonical_skill}``.
    """
    index: dict[str, str] = {}
    for row in alias_rows:
        canonical = row["canonical_skill"]
        index[normalize_skill(row["alias"])] = canonical
        index[normalize_skill(canonical)] = canonical
    return index


def user_canonical_skills(
    resume_text: str | None, alias_rows: Sequence[Mapping[str, Any]]
) -> set[str]:
    """Return the set of canonical skills the user demonstrably has.

    A canonical skill is "had" if the canonical name OR any of its aliases
    appears (token-boundary match) in the resume/profile text.
    """
    if not resume_text:
        return set()
    haystack = _padded(resume_text)
    present: set[str] = set()
    for row in alias_rows:
        canonical = row["canonical_skill"]
        surface_forms = (row["alias"], canonical)
        if any(f" {normalize_skill(s)} " in haystack for s in surface_forms):
            present.add(canonical)
    return present


def match_skills(
    skills: Iterable[str],
    alias_rows: Sequence[Mapping[str, Any]],
    resume_text: str | None,
) -> tuple[list[str], list[str]]:
    """Split a list of listing skills into (matched, missed).

    A skill matches if either:

    1. it maps through the alias table to a canonical skill the user has
       (the primary, controlled-vocabulary path), or
    2. its normalized form appears directly in the resume text (a documented
       fallback so a skill literally on the resume but not yet aliased still
       counts — also fully deterministic and explainable).

    Original skill strings are preserved in the output for display. Order is
    preserved for determinism.
    """
    index = build_alias_index(alias_rows)
    user_skills = user_canonical_skills(resume_text, alias_rows)
    haystack = _padded(resume_text) if resume_text else " "

    matched: list[str] = []
    missed: list[str] = []
    for skill in skills:
        norm = normalize_skill(skill)
        canonical = index.get(norm)
        is_match = (canonical is not None and canonical in user_skills) or (
            bool(norm) and f" {norm} " in haystack
        )
        (matched if is_match else missed).append(skill)
    return matched, missed


def coverage(matched: Sequence[str], missed: Sequence[str]) -> float:
    """Fraction of skills matched.

    Returns ``1.0`` when there are no skills to match (nothing required means
    fully covered), avoiding a divide-by-zero and keeping the sub-score neutral.
    """
    total = len(matched) + len(missed)
    if total == 0:
        return 1.0
    return len(matched) / total
