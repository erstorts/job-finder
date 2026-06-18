"""Deterministic deduplication — normalization, blocking, and matching.

This is the G1 ("never apply twice") guard. Dedup is a record-linkage problem:
a *job* is the real role, a *listing* is one sighting of it (PRD §5). The
technique is **blocking then matching** (PRD §9.1): bucket candidates by a cheap
key (normalized company name), then run the careful fuzzy comparison only inside
that bucket.

Everything here is a pure, deterministic function of its inputs — no clock, no
randomness, no network (REPEAT1) — so every merge decision is explainable
("merged because same normalized company and title ratio 0.94").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from rapidfuzz import fuzz

# Legal suffixes stripped from company names before blocking. They carry no
# identity ("Acme Inc." and "Acme LLC" are the same employer for our purposes).
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "l.l.c", "ltd", "limited", "corp",
    "corporation", "co", "company", "gmbh", "plc", "llp", "lp", "sa", "ag",
    "pte", "pvt", "holdings", "group",
}

# Company names that are not real identities and break the blocking key
# (DEDUP-E1). When the normalized company is empty or one of these, we fall
# back to a title + jd_text comparison and default to "borderline".
_GENERIC_COMPANIES = {
    "confidential", "undisclosed", "stealth", "private", "n/a", "na",
    "staffing", "recruiting", "recruiter", "agency", "consulting",
}

# Title abbreviations expanded so "Sr. SWE" and "Senior Software Engineer"
# normalize toward each other. Applied token-by-token.
_TITLE_ABBREVIATIONS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "jnr": "junior",
    "mgr": "manager",
    "eng": "engineer",
    "engr": "engineer",
    "dev": "developer",
    "swe": "software engineer",
    "sde": "software engineer",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "ds": "data scientist",
    "de": "data engineer",
    "ii": "2",
    "iii": "3",
    "iv": "4",
}


def _strip_punctuation(text: str) -> str:
    """Lowercase, replace any non-alphanumeric run with a single space, trim."""
    lowered = text.lower()
    # Keep digits: seniority levels ("engineer 2") are meaningful for matching.
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return cleaned.strip()


def normalize_company(name: str | None) -> str:
    """Normalize a company name into its blocking key.

    Lowercases, strips punctuation, and removes trailing legal suffixes
    ("Inc.", "LLC", ...) so the same employer buckets together regardless of
    cosmetic differences. Returns ``""`` for ``None``/blank input.
    """
    if not name:
        return ""
    cleaned = _strip_punctuation(name)
    tokens = [t for t in cleaned.split() if t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def normalize_title(title: str | None) -> str:
    """Normalize a job title for fuzzy comparison.

    Lowercases, strips punctuation, and expands common abbreviations
    ("Sr." -> "senior") token-by-token. Returns ``""`` for ``None``/blank input.
    """
    if not title:
        return ""
    cleaned = _strip_punctuation(title)
    expanded: list[str] = []
    for token in cleaned.split():
        # An abbreviation may expand to multiple words (e.g. "swe").
        expanded.extend(_TITLE_ABBREVIATIONS.get(token, token).split())
    return " ".join(expanded)


def is_generic_company(company_norm: str) -> bool:
    """Whether a normalized company name is too generic to block on (DEDUP-E1)."""
    if not company_norm:
        return True
    tokens = set(company_norm.split())
    return bool(tokens & _GENERIC_COMPANIES)


@dataclass(frozen=True)
class MatchResult:
    """Outcome of a dedup lookup.

    Attributes
    ----------
    status:
        One of ``"new_job"``, ``"confident_duplicate"``, ``"borderline"``.
    job_id:
        The matched existing job id, or ``None`` for a new job.
    score:
        The best title-similarity ratio found (0-100), for display/explanation.
    reason:
        Human-readable explanation of the decision (defensibility, PRD §9.1).
    """

    status: str
    job_id: int | None
    score: float
    reason: str


def find_duplicate(
    new_extraction: Mapping[str, Any],
    existing_jobs: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> MatchResult:
    """Decide whether a freshly extracted listing duplicates an existing job.

    Parameters
    ----------
    new_extraction:
        Mapping with at least ``company_name`` and ``title``; ``jd_text`` is
        used only in the generic-company fallback.
    existing_jobs:
        Sequence of mappings with ``id``, ``company_name_norm``, ``title_norm``
        and optionally ``jd_text`` (the precomputed normalized fields from the
        ``job`` table).
    config:
        Parsed config; ``config["dedup"]`` supplies ``confident_duplicate`` and
        ``borderline_floor`` thresholds.

    Returns
    -------
    MatchResult
        The decision. Thresholds (defaults 90 / 75) come entirely from config
        (REPEAT3).
    """
    thresholds = config["dedup"]
    confident = thresholds["confident_duplicate"]
    borderline = thresholds["borderline_floor"]

    company_norm = normalize_company(new_extraction.get("company_name"))
    title_norm = normalize_title(new_extraction.get("title"))

    # --- DEDUP-E1 fallback: generic/missing company breaks the blocking key.
    # Compare titles (and jd_text as a tiebreak) across ALL jobs and default to
    # borderline so the user decides; never auto-confirm without a real company.
    if is_generic_company(company_norm):
        return _match_without_company(
            title_norm, new_extraction.get("jd_text"), existing_jobs, borderline
        )

    # --- Normal path: BLOCK on company, then MATCH on title within the bucket.
    candidates = [
        j for j in existing_jobs if j.get("company_name_norm") == company_norm
    ]
    if not candidates:
        return MatchResult("new_job", None, 0.0,
                           "No existing job shares this company.")

    best_job, best_score = _best_title_match(title_norm, candidates)

    if best_score >= confident:
        return MatchResult(
            "confident_duplicate", best_job["id"], best_score,
            f"Same normalized company and title ratio {best_score:.0f} "
            f">= {confident}.",
        )
    if best_score >= borderline:
        return MatchResult(
            "borderline", best_job["id"], best_score,
            f"Same company but title ratio {best_score:.0f} is between "
            f"{borderline} and {confident}; confirm or mark distinct.",
        )
    return MatchResult(
        "new_job", None, best_score,
        f"Same company but best title ratio {best_score:.0f} < {borderline}.",
    )


def _best_title_match(
    title_norm: str, candidates: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any], float]:
    """Return the candidate with the highest token_sort_ratio on title_norm.

    token_sort_ratio is order-insensitive, so "engineer data senior" and
    "senior data engineer" score as identical — robust to word reordering.
    """
    best_job = candidates[0]
    best_score = -1.0
    for job in candidates:
        score = fuzz.token_sort_ratio(title_norm, job.get("title_norm") or "")
        if score > best_score:
            best_score, best_job = score, job
    return best_job, best_score


def _match_without_company(
    title_norm: str,
    jd_text: str | None,
    existing_jobs: Sequence[Mapping[str, Any]],
    borderline: float,
) -> MatchResult:
    """Generic-company fallback (DEDUP-E1).

    With no reliable company key we cannot block, so we scan all jobs for a
    plausible title match and add a jd_text similarity signal. We never return
    ``confident_duplicate`` here — a missing/confidential company is exactly the
    case where the user should make the final call, so the strongest verdict is
    ``borderline``.
    """
    if not existing_jobs:
        return MatchResult("new_job", None, 0.0, "No existing jobs to compare.")

    best_job, best_title = _best_title_match(title_norm, existing_jobs)
    if best_title < borderline:
        return MatchResult(
            "new_job", None, best_title,
            "Generic/confidential company and no close title match.",
        )

    # Title is close enough to be suspicious; fold in jd_text similarity for the
    # explanation, then defer to the user.
    jd_sim = 0.0
    if jd_text and best_job.get("jd_text"):
        jd_sim = fuzz.token_set_ratio(jd_text, best_job["jd_text"])
    return MatchResult(
        "borderline", best_job["id"], best_title,
        f"Generic/confidential company; title ratio {best_title:.0f} "
        f"(jd similarity {jd_sim:.0f}). Confirm or mark distinct.",
    )
