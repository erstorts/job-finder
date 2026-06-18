"""Determinism tests for deduplication (PRD REPEAT4, §9.1)."""

from __future__ import annotations

from src import dedup

CONFIG = {"dedup": {"confident_duplicate": 90, "borderline_floor": 75}}

EXISTING = [
    {
        "id": 1,
        "company_name_norm": "acme",
        "title_norm": "senior data engineer",
        "jd_text": "Build pipelines in Python and Airflow.",
    },
    {
        "id": 2,
        "company_name_norm": "globex",
        "title_norm": "machine learning engineer",
        "jd_text": "Train models.",
    },
]


def test_normalize_company_strips_legal_suffix() -> None:
    assert dedup.normalize_company("Acme, Inc.") == "acme"
    assert dedup.normalize_company("Globex LLC") == "globex"
    assert dedup.normalize_company(None) == ""


def test_normalize_title_expands_abbreviations() -> None:
    assert dedup.normalize_title("Sr. Data Engineer") == "senior data engineer"
    assert dedup.normalize_title("ML Engineer") == "machine learning engineer"


def test_confident_duplicate() -> None:
    # Same company, same title with cosmetic differences -> confident duplicate.
    extraction = {"company_name": "ACME Inc.", "title": "Senior Data Engineer"}
    result = dedup.find_duplicate(extraction, EXISTING, CONFIG)
    assert result.status == "confident_duplicate"
    assert result.job_id == 1
    assert result.score >= 90


def test_borderline_match() -> None:
    # Same company, related-but-different title lands in the borderline band.
    extraction = {"company_name": "Acme", "title": "Senior Data Engineer, Platform"}
    result = dedup.find_duplicate(extraction, EXISTING, CONFIG)
    assert result.status == "borderline"
    assert result.job_id == 1
    assert 75 <= result.score < 90


def test_new_job_different_company() -> None:
    extraction = {"company_name": "Initech", "title": "Senior Data Engineer"}
    result = dedup.find_duplicate(extraction, EXISTING, CONFIG)
    assert result.status == "new_job"
    assert result.job_id is None


def test_generic_company_falls_back_to_borderline() -> None:
    # DEDUP-E1: confidential company never yields a confident duplicate.
    extraction = {
        "company_name": "Confidential",
        "title": "Senior Data Engineer",
        "jd_text": "Build pipelines in Python and Airflow.",
    }
    result = dedup.find_duplicate(extraction, EXISTING, CONFIG)
    assert result.status == "borderline"


def test_dedup_is_deterministic() -> None:
    extraction = {"company_name": "ACME Inc.", "title": "Senior Data Engineer"}
    r1 = dedup.find_duplicate(extraction, EXISTING, CONFIG)
    r2 = dedup.find_duplicate(extraction, EXISTING, CONFIG)
    assert r1 == r2
