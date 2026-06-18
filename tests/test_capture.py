"""Integration test for the capture + dedup + status flow (PRD T3, §6).

Exercises the data-access layer end to end without Streamlit: insert a job,
attach listings, append status events, and confirm dedup finds the duplicate
and current_status derives from the latest event.
"""

from __future__ import annotations

import json

from src import dedup
from src.config import get_config
from src.db import (
    add_listing,
    add_status_event,
    current_status,
    get_connection,
    get_dedup_candidates,
    init_db,
    insert_job,
)

CONFIG = get_config()


def _db():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _capture(conn, company, title, source, jd="Pipelines in Python."):
    extraction = {"company_name": company, "title": title}
    job_id = insert_job(
        conn,
        company_name=company, title=title, location=None, remote_flag=None,
        jd_text=jd, salary_min=None, salary_max=None, benefits=None,
        company_description=None, extracted_json=json.dumps(extraction),
    )
    add_listing(conn, job_id, source_site=source, url=None, raw_title=title)
    # Stamp 'found' explicitly so tests are independent of the wall clock.
    add_status_event(conn, job_id, "found", occurred_at="2026-06-01T09:00:00")
    return job_id


def test_norm_keys_persisted() -> None:
    conn = _db()
    job_id = _capture(conn, "Acme, Inc.", "Sr. Data Engineer", "linkedin")
    row = conn.execute(
        "SELECT company_name_norm, title_norm FROM job WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["company_name_norm"] == "acme"
    assert row["title_norm"] == "senior data engineer"


def test_dedup_finds_existing_via_db_candidates() -> None:
    conn = _db()
    _capture(conn, "Acme, Inc.", "Senior Data Engineer", "linkedin")
    # Same job seen on a different site with a cosmetic title variant (G1).
    result = dedup.find_duplicate(
        {"company_name": "ACME", "title": "Sr Data Engineer"},
        get_dedup_candidates(conn),
        CONFIG,
    )
    assert result.status == "confident_duplicate"


def test_current_status_is_latest_event() -> None:
    conn = _db()
    job_id = _capture(conn, "Globex", "ML Engineer", "greenhouse")
    add_status_event(conn, job_id, "applied", occurred_at="2026-06-10T09:00:00")
    add_status_event(conn, job_id, "recruiter_screen", occurred_at="2026-06-12T09:00:00")
    # Backwards move recorded later in time wins (event log, not a column).
    add_status_event(conn, job_id, "rejected", occurred_at="2026-06-14T09:00:00")
    assert current_status(conn, job_id) == "rejected"
    # History is intact, never overwritten.
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM status_event WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert count == 4  # found + 3
