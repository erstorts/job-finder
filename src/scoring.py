"""Deterministic match scoring — hard gates then weighted soft criteria.

Scoring is a pure function of the frozen extraction, the profile, and the rubric
config (PRD §9.2, REPEAT1). The LLM never produces a score (DECISIONS.md #3):
every point traces to a reason, and the same inputs always yield the same number
and the same breakdown.

Two stages:

1. **Hard gates.** If any gate fails the recommendation is ``"pass"`` and the
   failing gate is recorded. (The composite is still computed for transparency.)
2. **Weighted soft criteria** produce a 0-100 composite, compared to the
   configurable apply threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src import skills

# Degree keywords used to infer whether the user holds a degree from resume
# text. The profile schema has no explicit degree field (PRD §6), so this is the
# documented deterministic workaround: presence of any of these tokens means
# "has a degree" for the degree hard gate.
_DEGREE_KEYWORDS = (
    "bachelor", "master", "phd", "ph.d", "doctorate", "mba",
    "b.s", "m.s", "b.a", "m.a", "bsc", "msc", "b.eng", "m.eng",
    "undergraduate", "graduate degree", "degree in",
)


def _bands(config: Mapping[str, Any]) -> list[str]:
    return config["seniority"]["bands"]


def band_distance(
    a: str | None, b: str | None, config: Mapping[str, Any]
) -> int | None:
    """Ordinal distance between two seniority bands, or ``None`` if unknown.

    ``None`` when either band is missing or not in the configured vocabulary —
    callers treat unknown distance as "cannot gate / neutral alignment".
    """
    bands = _bands(config)
    if a not in bands or b not in bands:
        return None
    return abs(bands.index(a) - bands.index(b))


def profile_has_degree(resume_text: str | None) -> bool:
    """Heuristic: does the resume indicate the user holds a degree?

    Deterministic substring scan for common degree tokens. Documented in §9.2 as
    a workaround for the absence of an explicit degree field on the profile.
    """
    if not resume_text:
        return False
    lowered = resume_text.lower()
    return any(keyword in lowered for keyword in _DEGREE_KEYWORDS)


def _json_list(raw: Any) -> list[str]:
    """Parse a JSON-array column (or passthrough a list) into a list of str."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


@dataclass(frozen=True)
class ScoreResult:
    """Result of scoring one job.

    Attributes
    ----------
    score:
        The 0-100 weighted composite.
    recommendation:
        ``"apply"`` or ``"pass"``.
    rubric_version:
        The config rubric version that produced this score (stored on the job).
    breakdown:
        JSON-serializable dict with every sub-score, the matched/missed skill
        lists, and all gate results — so the number is never shown alone (T5).
    """

    score: float
    recommendation: str
    rubric_version: str
    breakdown: dict[str, Any]


def _evaluate_gates(
    extraction: Mapping[str, Any],
    profile: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Run the hard gates and return one record per gate (passed + detail)."""
    gates: list[dict[str, Any]] = []

    # Gate 1 — degree. Fails if the listing requires a degree the user lacks.
    if extraction.get("degree_required") is True:
        has = profile_has_degree(profile.get("resume_text"))
        gates.append({
            "name": "degree",
            "passed": has,
            "detail": "Listing requires a degree; resume "
                      + ("indicates one." if has else "shows none."),
        })

    # Gate 2 — seniority. Fails if more than N bands from the target.
    dist = band_distance(
        extraction.get("seniority"), profile.get("target_seniority"), config
    )
    max_dist = config["scoring"]["seniority_gate_max_distance"]
    if dist is not None:
        gates.append({
            "name": "seniority",
            "passed": dist <= max_dist,
            "detail": f"Seniority is {dist} band(s) from target "
                      f"(max allowed {max_dist}).",
        })

    # Gate 3 — unmeetable hard constraints (clearance, citizenship, ...).
    unmeetable = [c.lower() for c in config["scoring"]["gates"]["unmeetable_constraints"]]
    hit = []
    for constraint in extraction.get("hard_constraints") or []:
        lc = str(constraint).lower()
        if any(u in lc for u in unmeetable):
            hit.append(constraint)
    if hit:
        gates.append({
            "name": "hard_constraint",
            "passed": False,
            "detail": f"Unmeetable constraint(s): {', '.join(hit)}.",
        })

    # Gate 4 — on-site in a non-target location while remote is not OK.
    remote = extraction.get("remote_flag")
    target_remote_ok = bool(profile.get("target_remote_ok"))
    location = (extraction.get("location") or "").lower()
    targets = [t.lower() for t in _json_list(profile.get("target_locations"))]
    if remote is False and not target_remote_ok and location:
        in_target = any(t in location or location in t for t in targets)
        if not in_target:
            gates.append({
                "name": "location",
                "passed": False,
                "detail": f"On-site in '{extraction.get('location')}', not a "
                          "target location, and remote is not OK.",
            })

    return gates


def score_job(
    extraction: Mapping[str, Any],
    profile: Mapping[str, Any],
    alias_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> ScoreResult:
    """Compute the match score, recommendation, and full breakdown.

    Parameters
    ----------
    extraction:
        The frozen extracted facts (dict form of the JobExtraction contract).
    profile:
        The single profile row as a dict.
    alias_rows:
        All ``skill_alias`` rows, for deterministic skill matching.
    config:
        Parsed config supplying weights, thresholds, and the rubric version.

    Returns
    -------
    ScoreResult
    """
    scoring_cfg = config["scoring"]
    weights = scoring_cfg["weights"]
    rubric_version = scoring_cfg["version"]
    threshold = scoring_cfg["apply_threshold"]
    resume_text = profile.get("resume_text")

    # --- Hard gates ---
    gates = _evaluate_gates(extraction, profile, config)
    gate_failed = any(not g["passed"] for g in gates)

    # --- Soft criteria ---
    matched_req, missed_req = skills.match_skills(
        extraction.get("required_skills") or [], alias_rows, resume_text
    )
    matched_pref, missed_pref = skills.match_skills(
        extraction.get("preferred_skills") or [], alias_rows, resume_text
    )

    sub_scores = {
        "required_skill_coverage": skills.coverage(matched_req, missed_req),
        "preferred_skill_coverage": skills.coverage(matched_pref, missed_pref),
        "seniority_alignment": _seniority_alignment(extraction, profile, config),
        "target_fit": _target_fit(extraction, profile),
        "comp_floor_met": _comp_floor_met(extraction, profile),
    }

    composite = round(
        100.0 * sum(weights[name] * value for name, value in sub_scores.items()),
        2,
    )

    # A failed gate forces "pass" regardless of the composite (PRD §9.2).
    if gate_failed:
        recommendation = "pass"
    else:
        recommendation = "apply" if composite >= threshold else "pass"

    breakdown = {
        "rubric_version": rubric_version,
        "apply_threshold": threshold,
        "composite": composite,
        "recommendation": recommendation,
        "gate_failed": gate_failed,
        "gates": gates,
        "weights": dict(weights),
        "sub_scores": sub_scores,
        "matched_required": matched_req,
        "missed_required": missed_req,
        "matched_preferred": matched_pref,
        "missed_preferred": missed_pref,
    }
    return ScoreResult(composite, recommendation, rubric_version, breakdown)


def _seniority_alignment(
    extraction: Mapping[str, Any],
    profile: Mapping[str, Any],
    config: Mapping[str, Any],
) -> float:
    """Closeness of job seniority to target band, 0..1 (0.5 when unknown)."""
    dist = band_distance(
        extraction.get("seniority"), profile.get("target_seniority"), config
    )
    if dist is None:
        return 0.5
    max_dist = max(1, len(_bands(config)) - 1)
    return max(0.0, 1.0 - dist / max_dist)


def _target_fit(
    extraction: Mapping[str, Any], profile: Mapping[str, Any]
) -> float:
    """Overlap of listing company_types with target company types, 0..1.

    Fraction of the user's target types present in the listing. ``0.5`` (neutral)
    when the target set is empty/unknown.
    """
    target = {t.lower() for t in _json_list(profile.get("target_company_types"))}
    if not target:
        return 0.5
    job_types = {str(t).lower() for t in (extraction.get("company_types") or [])}
    return len(target & job_types) / len(target)


def _comp_floor_met(
    extraction: Mapping[str, Any], profile: Mapping[str, Any]
) -> float:
    """Whether the salary range clears the comp floor: 1.0 / 0.0 / 0.5 unknown."""
    floor = profile.get("target_min_comp")
    if not floor:
        return 0.5  # no floor set -> unknown/neutral
    top = extraction.get("salary_max")
    bottom = extraction.get("salary_min")
    reference = top if top is not None else bottom
    if reference is None:
        return 0.5  # salary absent in listing -> unknown
    return 1.0 if reference >= floor else 0.0
