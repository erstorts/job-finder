"""Determinism tests for the ATS keyword-match scoring.

Uses the real config.toml so the locked expected values track the shipped rubric
(required 0.70 / preferred 0.30 weights, bands strong=80 / moderate=50).
"""

from __future__ import annotations

from src import scoring
from src.config import get_config

CONFIG = get_config()

ALIASES = [
    {"canonical_skill": "python", "alias": "py"},
    {"canonical_skill": "orchestration", "alias": "airflow"},
    {"canonical_skill": "sql", "alias": "postgres"},
]

PROFILE = {
    "resume_text": "Built pipelines with python, airflow, sql.",
    "linkedin_text": None,
}

# Required both matched (coverage 1.0), preferred missed (coverage 0.0).
#   100 * (1.0*0.70 + 0.0*0.30) / (0.70+0.30) = 70.0 -> moderate band.
EXTRACTION = {
    "required_skills": ["Python", "Airflow"],
    "preferred_skills": ["Spark"],
}


def test_score_is_exact_and_repeatable() -> None:
    r1 = scoring.score_job(EXTRACTION, PROFILE, ALIASES, CONFIG)
    r2 = scoring.score_job(EXTRACTION, PROFILE, ALIASES, CONFIG)
    assert r1.score == 70.0
    assert r1.band == "moderate"
    assert r1.breakdown == r2.breakdown  # full determinism, not just the number


def test_breakdown_records_matched_and_missing() -> None:
    r = scoring.score_job(EXTRACTION, PROFILE, ALIASES, CONFIG)
    assert r.breakdown["matched_required"] == ["Python", "Airflow"]
    assert r.breakdown["missed_preferred"] == ["Spark"]
    assert r.breakdown["required_coverage"] == 1.0
    # The missing-keywords list is what to add to the resume/LinkedIn.
    assert r.breakdown["missing_keywords"] == ["Spark"]


def test_linkedin_text_is_not_scored() -> None:
    # Airflow appears only in LinkedIn text, which the ATS score ignores, so it
    # stays a miss and required coverage is only 1/2.
    profile = {"resume_text": "python sql", "linkedin_text": "airflow orchestration"}
    r = scoring.score_job(EXTRACTION, profile, ALIASES, CONFIG)
    assert r.breakdown["matched_required"] == ["Python"]
    assert r.breakdown["missed_required"] == ["Airflow"]
    assert r.breakdown["required_coverage"] == 0.5


def test_only_required_list_is_scored_alone() -> None:
    # No preferred list -> score is pure required coverage (renormalized).
    extraction = {"required_skills": ["Python"], "preferred_skills": []}
    r = scoring.score_job(extraction, PROFILE, ALIASES, CONFIG)
    assert r.score == 100.0
    assert r.band == "strong"


def test_no_skills_is_unscorable() -> None:
    r = scoring.score_job({"required_skills": [], "preferred_skills": []}, PROFILE, ALIASES, CONFIG)
    assert r.score == 0.0
    assert r.breakdown["scorable"] is False


def test_band_for_thresholds() -> None:
    assert scoring.band_for(80, CONFIG) == "strong"
    assert scoring.band_for(50, CONFIG) == "moderate"
    assert scoring.band_for(49, CONFIG) == "weak"
