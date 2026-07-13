"""Tests for the outcome analytics (§A)."""

from __future__ import annotations

from src import analytics


def _record(job_id, *, score, salary, location, cover_letter, linkedin, sources,
            posted, seen, stages):
    """Build a synthetic record; stages is a list of (status, iso_time)."""
    return {
        "job_id": job_id,
        "match_score": score,
        "salary_min": salary,
        "location_type": location,
        "cover_letter_option": cover_letter,
        "linkedin_contact": linkedin,
        "sources": sources,
        "date_posted": posted,
        "date_first_seen": seen,
        "events": [{"status": s, "occurred_at": t} for s, t in stages],
    }


RECORDS = [
    # Applied + interviewed: linkedin, denver, 120k, high score, cover letter,
    # found someone on linkedin.
    _record(1, score=85, salary=120_000, location="denver", cover_letter=True,
            linkedin=True, sources=["linkedin"], posted="2026-06-01",
            seen="2026-06-08", stages=[
                ("found", "2026-06-08T09:00:00"),
                ("applied", "2026-06-09T09:00:00"),
                ("landed_interview", "2026-06-14T09:00:00"),
            ]),
    # Applied + rejected: greenhouse, remote, 90k, medium score, no cover letter.
    _record(2, score=70, salary=90_000, location="remote", cover_letter=False,
            linkedin=False, sources=["greenhouse"], posted="2026-06-01",
            seen="2026-06-05", stages=[
                ("found", "2026-06-05T09:00:00"),
                ("applied", "2026-06-06T09:00:00"),
                ("rejected", "2026-06-20T09:00:00"),
            ]),
    # Found only, never applied -> excluded from outcome analysis.
    _record(3, score=40, salary=None, location="remote", cover_letter=False,
            linkedin=False, sources=["linkedin"], posted="2026-06-01",
            seen="2026-06-02", stages=[("found", "2026-06-02T09:00:00")]),
]


def test_outcome_of() -> None:
    assert analytics.outcome_of(RECORDS[0]) == "interview"
    assert analytics.outcome_of(RECORDS[1]) == "rejected"
    assert analytics.outcome_of(RECORDS[2]) is None  # never applied


def test_outcome_totals_excludes_never_applied() -> None:
    totals = analytics.outcome_totals(RECORDS)
    assert totals == {"interview": 1, "rejected": 1, "ghosted": 0, "pending": 0}


def test_days_since_posted() -> None:
    assert analytics.days_since_posted(RECORDS[0]) == 7  # 06-01 -> 06-08
    assert analytics.days_since_posted(RECORDS[1]) == 4


def test_bands() -> None:
    assert analytics.score_band(85) == "high (80+)"
    assert analytics.score_band(70) == "medium (50-79)"
    assert analytics.score_band(40) == "low (<50)"
    assert analytics.salary_band(120_000) == "100-150k"
    assert analytics.salary_band(None) == "unknown"
    assert analytics.posting_age_band(7) == "≤1 week"
    assert analytics.posting_age_band(20) == "1-4 weeks"


def test_segment_by_source_conditions_on_applied() -> None:
    rows = {r["segment"]: r for r in analytics.segment_outcomes(RECORDS, "source")}
    # Job #3 (found only, linkedin) is excluded, so linkedin has n=1 (job #1).
    assert rows["linkedin"]["applied"] == 1
    assert rows["linkedin"]["interview"] == 1
    assert rows["linkedin"]["interview_rate"] == 1.0
    assert rows["greenhouse"]["applied"] == 1
    assert rows["greenhouse"]["rejected"] == 1
    assert rows["greenhouse"]["interview_rate"] == 0.0


def test_segment_by_location_and_linkedin() -> None:
    loc = {r["segment"]: r for r in analytics.segment_outcomes(RECORDS, "location")}
    assert loc["denver"]["interview"] == 1
    assert loc["remote"]["applied"] == 1 and loc["remote"]["interview"] == 0

    li = {r["segment"]: r for r in analytics.segment_outcomes(RECORDS, "linkedin_contact")}
    assert li["yes"]["interview"] == 1
    assert li["no"]["interview"] == 0
