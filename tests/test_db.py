"""Foundation smoke tests (PRD §13 M1 acceptance: app creates the database).

Algorithm/determinism tests (test_dedup, test_scoring, test_skills) arrive with
their modules in M3 per REPEAT4.
"""

from __future__ import annotations

import sqlite3

from src.db import (
    STATUS_SEED,
    add_skill_alias,
    delete_skill_alias,
    get_connection,
    get_profile,
    init_db,
    list_skill_aliases,
    save_profile,
    update_skill_alias,
)

EXPECTED_TABLES = {
    "profile",
    "skill_alias",
    "job",
    "listing",
    "status",
    "status_event",
    "application",
}


def _fresh_db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_init_creates_all_tables() -> None:
    conn = _fresh_db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_status_table_seeded() -> None:
    conn = _fresh_db()
    rows = conn.execute(
        "SELECT name, sort_order, is_terminal FROM status ORDER BY sort_order"
    ).fetchall()
    assert len(rows) == len(STATUS_SEED)
    # Terminal stages: passed at triage + the two negative outcomes.
    terminal = {r["name"] for r in rows if r["is_terminal"] == 1}
    assert terminal == {"passed", "rejected", "ghosted"}


def test_init_is_idempotent() -> None:
    conn = _fresh_db()
    # Running again must not duplicate status rows or error.
    init_db(conn)
    (count,) = conn.execute("SELECT COUNT(*) FROM status").fetchone()
    assert count == len(STATUS_SEED)


def test_profile_upsert_keeps_single_row() -> None:
    conn = _fresh_db()
    assert get_profile(conn) is None  # nothing saved yet

    save_profile(conn, resume_text="python sql airflow", linkedin_text="li text")
    p = get_profile(conn)
    assert p is not None
    assert p["resume_text"] == "python sql airflow"
    assert p["linkedin_text"] == "li text"

    # Saving again must update in place, not create a second row.
    save_profile(conn, resume_text="python sql spark", linkedin_text=None)
    (count,) = conn.execute("SELECT COUNT(*) FROM profile").fetchone()
    assert count == 1
    assert get_profile(conn)["resume_text"] == "python sql spark"


def test_skill_alias_crud() -> None:
    conn = _fresh_db()
    assert add_skill_alias(conn, "orchestration", "airflow") is True
    # Duplicate pair is ignored, not an error.
    assert add_skill_alias(conn, "orchestration", "airflow") is False
    add_skill_alias(conn, "orchestration", "prefect")

    rows = list_skill_aliases(conn)
    assert {(r["canonical_skill"], r["alias"]) for r in rows} == {
        ("orchestration", "airflow"),
        ("orchestration", "prefect"),
    }

    airflow_id = next(r["id"] for r in rows if r["alias"] == "airflow")
    update_skill_alias(conn, airflow_id, "orchestration", "apache airflow")
    delete_skill_alias(conn, airflow_id)
    remaining = {r["alias"] for r in list_skill_aliases(conn)}
    assert remaining == {"prefect"}


def test_foreign_keys_enforced() -> None:
    conn = _fresh_db()
    # A status_event pointing at a non-existent job must be rejected.
    try:
        conn.execute(
            "INSERT INTO status_event (job_id, status, occurred_at) "
            "VALUES (?, ?, ?)",
            (999, "found", "2026-06-15T00:00:00"),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "foreign_keys pragma should reject orphan status_event"
