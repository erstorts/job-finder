"""ATS match scoring — how well the resume + LinkedIn cover a listing's skills.

Scoring is a pure function of the frozen extraction, the profile text, the skill
alias vocabulary, and the rubric config. The LLM never produces a score: every
point traces to a keyword, and the same inputs always yield the same number and
the same breakdown.

The score answers "would this application clear a keyword-matching ATS, and if
not, what am I missing?" It is a weighted blend of required- and
preferred-skill coverage of the resume/LinkedIn text. The breakdown lists the
missing keywords so the user knows exactly what to add to their resume or
LinkedIn before applying (or whether to pass).

There are deliberately no hard gates here — triage is an evaluation aid, not an
apply/pass verdict. The user decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src import skills


@dataclass(frozen=True)
class ScoreResult:
    """Result of scoring one job against the profile.

    Attributes
    ----------
    score:
        The 0-100 weighted keyword-coverage composite.
    band:
        Coarse guidance label: ``"strong"`` | ``"moderate"`` | ``"weak"``.
    rubric_version:
        The config rubric version that produced this score (stored on the job).
    breakdown:
        JSON-serializable dict with matched/missed required and preferred
        keywords, per-list coverage, the combined list of missing keywords, and
        the weights — so the number is never shown alone.
    """

    score: float
    band: str
    rubric_version: str
    breakdown: dict[str, Any]


def _resume_haystack(profile: Mapping[str, Any]) -> str:
    """The text the keyword match runs against — the resume only.

    LinkedIn text is deliberately excluded: the ATS score reflects purely what a
    resume-scanning ATS would see. An empty/absent resume yields an empty
    haystack (nothing matches).
    """
    return profile.get("resume_text") or ""


def band_for(score: float, config: Mapping[str, Any]) -> str:
    """Map a 0-100 score to a guidance band using the configured thresholds."""
    bands = config["scoring"]["bands"]
    if score >= bands["strong"]:
        return "strong"
    if score >= bands["moderate"]:
        return "moderate"
    return "weak"


def score_job(
    extraction: Mapping[str, Any],
    profile: Mapping[str, Any],
    alias_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> ScoreResult:
    """Compute the ATS match score, band, and full keyword breakdown.

    Parameters
    ----------
    extraction:
        The frozen extracted facts (dict form of the JobExtraction contract).
    profile:
        The single profile row as a dict (``resume_text`` + ``linkedin_text``).
    alias_rows:
        All ``skill_alias`` rows, for deterministic skill matching.
    config:
        Parsed config supplying the coverage weights and the rubric version.

    Returns
    -------
    ScoreResult
    """
    scoring_cfg = config["scoring"]
    weights = scoring_cfg["weights"]
    rubric_version = scoring_cfg["version"]
    haystack = _resume_haystack(profile)

    required = extraction.get("required_skills") or []
    preferred = extraction.get("preferred_skills") or []

    matched_req, missed_req = skills.match_skills(required, alias_rows, haystack)
    matched_pref, missed_pref = skills.match_skills(preferred, alias_rows, haystack)

    req_cov = skills.coverage(matched_req, missed_req)
    pref_cov = skills.coverage(matched_pref, missed_pref)

    # Weight required vs preferred, renormalizing so a listing with only one of
    # the two lists is scored purely on that list (an empty list is neutral and
    # would otherwise inflate the blend). No skills at all -> unscorable (0).
    contributions: list[tuple[float, float]] = []
    if required:
        contributions.append((req_cov, weights["required_skill_coverage"]))
    if preferred:
        contributions.append((pref_cov, weights["preferred_skill_coverage"]))

    if contributions:
        total_w = sum(w for _, w in contributions)
        composite = round(
            100.0 * sum(cov * w for cov, w in contributions) / total_w, 2
        )
    else:
        composite = 0.0

    band = band_for(composite, config)

    # Missing keywords across both lists — exactly what to add to resume/LinkedIn.
    missing_keywords = missed_req + missed_pref

    breakdown = {
        "rubric_version": rubric_version,
        "composite": composite,
        "band": band,
        "weights": dict(weights),
        "bands": dict(scoring_cfg["bands"]),
        "required_coverage": round(req_cov, 3),
        "preferred_coverage": round(pref_cov, 3),
        "matched_required": matched_req,
        "missed_required": missed_req,
        "matched_preferred": matched_pref,
        "missed_preferred": missed_pref,
        "missing_keywords": missing_keywords,
        "scorable": bool(contributions),
    }
    return ScoreResult(composite, band, rubric_version, breakdown)
