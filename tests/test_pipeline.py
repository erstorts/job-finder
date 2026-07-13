"""Tests for pipeline staleness flags and application logging (PRD P2, P3, L2)."""

from __future__ import annotations

from datetime import datetime

from src import pipeline
from src.config import get_config
from src.db import (
    add_status_event,
    current_status,
    get_application,
    get_connection,
    init_db,
    insert_job,
    list_pipeline,
    list_todo,
    save_application,
    set_score,
)

CONFIG = get_config()  # followup 7d, ghosted 21d


def test_days_since_is_deterministic_with_injected_now() -> None:
    now = datetime(2026, 6, 15, 12, 0, 0)
    assert pipeline.days_since("2026-06-08T12:00:00", now=now) == 7
    assert pipeline.days_since(None, now=now) is None
    assert pipeline.days_since("garbage", now=now) is None


def test_flags_thresholds() -> None:
    assert pipeline.flags_for(3, False, CONFIG) == {
        "needs_followup": False, "likely_ghosted": False}
    assert pipeline.flags_for(7, False, CONFIG)["needs_followup"] is True
    flagged = pipeline.flags_for(21, False, CONFIG)
    assert flagged["needs_followup"] and flagged["likely_ghosted"]


def test_terminal_status_never_flagged() -> None:
    # Even long-idle terminal jobs need no follow-up.
    assert pipeline.flags_for(100, True, CONFIG) == {
        "needs_followup": False, "likely_ghosted": False}


def _db():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_application_logging_and_pipeline_row() -> None:
    conn = _db()
    job_id = insert_job(
        conn, company_name="Acme", title="Data Engineer", location_type="remote",
        jd_text=None, salary_min=None, salary_max=None,
        cover_letter_option=None, date_posted=None, extracted_json="{}",
    )
    add_status_event(conn, job_id, "found", occurred_at="2026-06-01T09:00:00")

    save_application(
        conn, job_id, applied_at="2026-06-02T10:00:00", linkedin_contact=1,
    )
    add_status_event(conn, job_id, "applied", occurred_at="2026-06-02T10:00:00")

    app = get_application(conn, job_id)
    assert app["linkedin_contact"] == 1
    assert current_status(conn, job_id) == "applied"

    pipe = list_pipeline(conn)
    assert len(pipe) == 1
    assert pipe[0]["current_status"] == "applied"


def test_todo_ordering() -> None:
    conn = _db()

    def found(company, loc, cover, posted, score):
        jid = insert_job(
            conn, company_name=company, title="Eng", location_type=loc,
            jd_text=None, salary_min=None, salary_max=None,
            cover_letter_option=cover, date_posted=posted, extracted_json="{}",
        )
        set_score(conn, jid, score, "{}", "ats-v1")
        add_status_event(conn, jid, "found", occurred_at="2026-07-01T09:00:00")
        return jid

    # Denver-first, then no-cover-first, then freshest, then highest ATS.
    found("A", "remote", 1, "2026-07-10", 90)   # remote, cover yes
    found("B", "denver", 0, "2026-07-01", 50)   # denver, no cover, oldest
    found("C", "denver", 0, "2026-07-05", 80)   # denver, no cover
    found("D", "denver", 1, "2026-07-08", 95)   # denver, cover yes
    found("E", "remote", 0, "2026-07-09", 70)   # remote, no cover
    found("F", "denver", 0, "2026-07-05", 85)   # ties C on date -> higher score first

    order = [r["company_name"] for r in list_todo(conn)]
    # Denver group (F,C,B by no-cover+fresh+score, then D cover-yes), then remote (E no-cover, A).
    assert order == ["F", "C", "B", "D", "E", "A"]
