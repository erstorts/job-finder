"""Tests for the conversion-funnel analytics (PRD A1, A2)."""

from __future__ import annotations

from src import analytics


def _record(job_id, score, sources, labels, stages):
    """Build a synthetic record; stages is a list of (status, iso_time)."""
    return {
        "job_id": job_id,
        "match_score": score,
        "sources": sources,
        "labels": labels,
        "events": [{"status": s, "occurred_at": t} for s, t in stages],
    }


LABELS_YES = {"cover_letter": True, "tailored_resume": True, "referral": False}
LABELS_NO = {"cover_letter": False, "tailored_resume": False, "referral": False}

RECORDS = [
    # Applied + interviewed + offer, via linkedin, high score, cover letter.
    _record(1, 85, ["linkedin"], LABELS_YES, [
        ("found", "2026-06-01T09:00:00"),
        ("applied", "2026-06-02T09:00:00"),
        ("recruiter_screen", "2026-06-05T09:00:00"),
        ("offer", "2026-06-10T09:00:00"),
    ]),
    # Applied, no interview, via greenhouse, medium score, no labels.
    _record(2, 70, ["greenhouse"], LABELS_NO, [
        ("found", "2026-06-01T09:00:00"),
        ("applied", "2026-06-03T09:00:00"),
    ]),
    # Only found, never applied (excluded from segmentation).
    _record(3, 50, ["linkedin"], LABELS_NO, [
        ("found", "2026-06-01T09:00:00"),
    ]),
]


def test_funnel_counts_and_conversion() -> None:
    funnel = {row["stage"]: row for row in analytics.funnel_counts(RECORDS)}
    assert funnel["found"]["count"] == 3
    assert funnel["applied"]["count"] == 2
    assert funnel["recruiter_screen"]["count"] == 1
    assert funnel["offer"]["count"] == 1
    # applied/found = 2/3
    assert funnel["applied"]["conversion_from_prev"] == round(2 / 3, 3)


def test_avg_time_in_stage() -> None:
    tis = analytics.avg_time_in_stage(RECORDS)
    # Job1 found->applied = 1 day; Job2 found->applied = 2 days -> avg 1.5
    assert tis["found"] == 1.5
    # Job1 applied->recruiter_screen = 3 days (only job past applied)
    assert tis["applied"] == 3.0


def test_score_band() -> None:
    assert analytics.score_band(85) == "high (80+)"
    assert analytics.score_band(70) == "medium (60-79)"
    assert analytics.score_band(10) == "low (<60)"
    assert analytics.score_band(None) == "unknown"


def test_segment_by_source_conditions_on_applied() -> None:
    rows = {r["segment"]: r for r in analytics.segment_funnel(RECORDS, "source")}
    # Only applied jobs counted: linkedin job #1 applied (interviewed+offer),
    # greenhouse job #2 applied (no interview). Job #3 (found only) excluded.
    assert rows["linkedin"]["applied"] == 1
    assert rows["linkedin"]["interview_rate"] == 1.0
    assert rows["linkedin"]["offer_rate"] == 1.0
    assert rows["greenhouse"]["applied"] == 1
    assert rows["greenhouse"]["interview_rate"] == 0.0


def test_segment_by_label() -> None:
    rows = {r["segment"]: r for r in analytics.segment_funnel(RECORDS, "cover_letter")}
    assert rows["yes"]["interviews"] == 1   # job #1 had a cover letter and interviewed
    assert rows["no"]["interviews"] == 0    # job #2 no cover letter, no interview
