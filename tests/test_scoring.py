"""Determinism + gate tests for match scoring (PRD REPEAT4, §9.2).

Uses the real config.toml so the locked expected values track the shipped rubric.
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
    "resume_text": "Bachelor of Science. Built pipelines with python, airflow, sql.",
    "target_seniority": "senior",
    "target_company_types": '["saas"]',
    "target_min_comp": 100000,
    "target_remote_ok": 1,
    "target_locations": '["Remote"]',
}

# Required skills both matched, preferred missed; perfect seniority/fit/comp.
# Hand-computed composite (weights 0.45/0.20/0.15/0.15/0.05):
#   0.45*1.0 + 0.20*0.0 + 0.15*1.0 + 0.15*1.0 + 0.05*1.0 = 0.80 -> 80.0
GOOD_EXTRACTION = {
    "required_skills": ["Python", "Airflow"],
    "preferred_skills": ["Spark"],
    "seniority": "senior",
    "company_types": ["saas"],
    "salary_min": 120000,
    "salary_max": 150000,
    "degree_required": False,
    "hard_constraints": [],
    "remote_flag": True,
    "location": "Remote",
}


def test_composite_is_exact_and_repeatable() -> None:
    r1 = scoring.score_job(GOOD_EXTRACTION, PROFILE, ALIASES, CONFIG)
    r2 = scoring.score_job(GOOD_EXTRACTION, PROFILE, ALIASES, CONFIG)
    assert r1.score == 80.0
    assert r1.recommendation == "apply"
    assert r1.breakdown == r2.breakdown  # full determinism, not just the number


def test_breakdown_records_skills() -> None:
    r = scoring.score_job(GOOD_EXTRACTION, PROFILE, ALIASES, CONFIG)
    assert r.breakdown["matched_required"] == ["Python", "Airflow"]
    assert r.breakdown["missed_preferred"] == ["Spark"]
    assert r.breakdown["sub_scores"]["required_skill_coverage"] == 1.0


def test_degree_gate_forces_pass() -> None:
    profile_no_degree = {**PROFILE, "resume_text": "python airflow sql pipelines"}
    extraction = {**GOOD_EXTRACTION, "degree_required": True}
    r = scoring.score_job(extraction, profile_no_degree, ALIASES, CONFIG)
    assert r.recommendation == "pass"
    assert r.breakdown["gate_failed"] is True
    assert any(g["name"] == "degree" and not g["passed"] for g in r.breakdown["gates"])


def test_seniority_gate_forces_pass() -> None:
    extraction = {**GOOD_EXTRACTION, "seniority": "intern"}  # 3 bands from senior
    r = scoring.score_job(extraction, PROFILE, ALIASES, CONFIG)
    assert r.recommendation == "pass"
    assert any(g["name"] == "seniority" and not g["passed"] for g in r.breakdown["gates"])


def test_hard_constraint_gate_forces_pass() -> None:
    extraction = {**GOOD_EXTRACTION, "hard_constraints": ["US Citizenship required"]}
    r = scoring.score_job(extraction, PROFILE, ALIASES, CONFIG)
    assert r.recommendation == "pass"
    assert any(g["name"] == "hard_constraint" for g in r.breakdown["gates"])


def test_profile_has_degree() -> None:
    assert scoring.profile_has_degree("B.S. in Computer Science") is True
    assert scoring.profile_has_degree("self-taught, no formal schooling") is False


def test_band_distance() -> None:
    assert scoring.band_distance("senior", "senior", CONFIG) == 0
    assert scoring.band_distance("intern", "senior", CONFIG) == 3
    assert scoring.band_distance("unknown", "senior", CONFIG) is None
