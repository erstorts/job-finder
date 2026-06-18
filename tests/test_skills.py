"""Determinism tests for skill matching (PRD REPEAT4)."""

from __future__ import annotations

from src import skills

ALIASES = [
    {"canonical_skill": "orchestration", "alias": "airflow"},
    {"canonical_skill": "orchestration", "alias": "prefect"},
    {"canonical_skill": "python", "alias": "py"},
    {"canonical_skill": "warehouse", "alias": "snowflake"},
]

RESUME = "Built ETL with Airflow and Python. Loaded data into Snowflake daily."


def test_normalize_skill() -> None:
    assert skills.normalize_skill("Apache  Airflow!") == "apache airflow"
    assert skills.normalize_skill(None) == ""


def test_user_canonical_skills() -> None:
    have = skills.user_canonical_skills(RESUME, ALIASES)
    assert have == {"orchestration", "python", "warehouse"}


def test_match_via_alias() -> None:
    # "Prefect" is an alias of orchestration, which the user has (via Airflow).
    matched, missed = skills.match_skills(["Prefect", "Kubernetes"], ALIASES, RESUME)
    assert matched == ["Prefect"]
    assert missed == ["Kubernetes"]


def test_match_direct_presence_fallback() -> None:
    # "data" isn't aliased but appears verbatim in the resume.
    matched, missed = skills.match_skills(["data", "rust"], ALIASES, RESUME)
    assert matched == ["data"]
    assert missed == ["rust"]


def test_word_boundary_no_false_positive() -> None:
    # "py" is an alias for python, but must not match inside "occupy".
    matched, missed = skills.match_skills(["py"], ALIASES, "I occupy a seat")
    assert matched == []
    assert missed == ["py"]


def test_coverage_empty_is_full() -> None:
    assert skills.coverage([], []) == 1.0
    assert skills.coverage(["a", "b"], ["c"]) == 2 / 3


def test_match_is_deterministic() -> None:
    args = (["Airflow", "Spark", "Python"], ALIASES, RESUME)
    assert skills.match_skills(*args) == skills.match_skills(*args)
