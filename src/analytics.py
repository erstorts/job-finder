"""Outcome analytics — which inputs correlate with landing interviews (§A).

Pure computation over the per-job records assembled by ``db.analytics_dataset``.
For every applied job we derive a single outcome — interview / rejected /
ghosted / pending — and then compare that outcome across each independent
variable the user cares about: source, Denver vs remote, min salary, ATS score,
days since posted, whether a cover letter was an option, and whether they found
someone on LinkedIn to message.

No SQL, no Streamlit, no clock (posting age comes from the stored dates), so
this is unit-testable in isolation. The observational caveat (low-n, confounded)
is rendered by the page, not encoded here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

# Statuses that mean the job was applied to (mirrors db.APPLIED_STATUSES).
_APPLIED = {"applied", "landed_interview", "rejected", "ghosted"}


def _reached(record: Mapping[str, Any]) -> set[str]:
    """Set of statuses a job ever held."""
    return {e["status"] for e in record["events"]}


def outcome_of(record: Mapping[str, Any]) -> str | None:
    """Derive one outcome per job, or ``None`` if it was never applied to.

    Priority: an interview is the positive result and wins even if a rejection
    followed it; otherwise rejected, then ghosted, then still-pending.
    """
    reached = _reached(record)
    if not (reached & _APPLIED):
        return None
    if "landed_interview" in reached:
        return "interview"
    if "rejected" in reached:
        return "rejected"
    if "ghosted" in reached:
        return "ghosted"
    return "pending"


OUTCOMES = ("interview", "rejected", "ghosted", "pending")


def days_since_posted(record: Mapping[str, Any]) -> int | None:
    """Age of the posting when it was captured: date_first_seen - date_posted.

    ``None`` if either date is missing or unparseable. This is a fixed property
    of the job (how stale the listing already was), not a live-updating count.
    """
    posted, seen = record.get("date_posted"), record.get("date_first_seen")
    if not posted or not seen:
        return None
    try:
        t0 = datetime.fromisoformat(posted)
        t1 = datetime.fromisoformat(seen)
    except ValueError:
        return None
    return max(0, (t1 - t0).days)


def score_band(score: float | None) -> str:
    """Bucket an ATS score into a coarse band for segmentation."""
    if score is None:
        return "unknown"
    if score >= 80:
        return "high (80+)"
    if score >= 50:
        return "medium (50-79)"
    return "low (<50)"


def salary_band(salary_min: int | None) -> str:
    """Bucket a minimum salary into a coarse band."""
    if not salary_min:
        return "unknown"
    if salary_min < 100_000:
        return "<100k"
    if salary_min < 150_000:
        return "100-150k"
    return "150k+"


def posting_age_band(days: int | None) -> str:
    """Bucket the days-since-posted into a coarse band."""
    if days is None:
        return "unknown"
    if days <= 7:
        return "≤1 week"
    if days <= 30:
        return "1-4 weeks"
    return ">1 month"


def _segmenters() -> dict[str, Callable[[Mapping[str, Any]], list[str]]]:
    """Map an independent-variable name to a function yielding a record's keys.

    ``source`` returns one key per distinct source (a multi-source job counts in
    each); the rest return a single key.
    """
    return {
        "source": lambda r: r["sources"] or ["(unknown)"],
        "location": lambda r: [r.get("location_type") or "(unknown)"],
        "salary": lambda r: [salary_band(r.get("salary_min"))],
        "ats_score": lambda r: [score_band(r.get("match_score"))],
        "days_since_posted": lambda r: [posting_age_band(days_since_posted(r))],
        "cover_letter": lambda r: ["yes" if r.get("cover_letter_option") else "no"],
        "linkedin_contact": lambda r: ["yes" if r.get("linkedin_contact") else "no"],
    }


# (key, human label) pairs, in the order the analytics page renders them.
VARIABLES: tuple[tuple[str, str], ...] = (
    ("source", "Source"),
    ("location", "Denver vs remote"),
    ("salary", "Minimum salary"),
    ("ats_score", "ATS score"),
    ("days_since_posted", "Days since posted"),
    ("cover_letter", "Cover letter option"),
    ("linkedin_contact", "Found someone on LinkedIn"),
)


def segment_outcomes(
    records: Sequence[Mapping[str, Any]], by: str
) -> list[dict[str, Any]]:
    """Compare outcomes across one independent variable (§A).

    Among applied jobs, bucket by ``by`` and count each outcome plus the
    interview rate (interviews / applied). Rows are sorted by applied-count
    descending.

    Raises
    ------
    KeyError
        If ``by`` is not a known variable.
    """
    segmenter = _segmenters()[by]
    buckets: dict[str, dict[str, int]] = {}
    for record in records:
        outcome = outcome_of(record)
        if outcome is None:
            continue  # never applied — not part of outcome analysis
        for key in segmenter(record):
            b = buckets.setdefault(
                key, {"applied": 0, "interview": 0, "rejected": 0, "ghosted": 0, "pending": 0}
            )
            b["applied"] += 1
            b[outcome] += 1

    rows = []
    for segment, b in buckets.items():
        applied = b["applied"]
        rows.append({
            "segment": segment,
            "applied": applied,
            "interview": b["interview"],
            "rejected": b["rejected"],
            "ghosted": b["ghosted"],
            "pending": b["pending"],
            "interview_rate": round(b["interview"] / applied, 3) if applied else 0.0,
        })
    rows.sort(key=lambda r: r["applied"], reverse=True)
    return rows


def outcome_totals(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count each outcome across all applied jobs (headline summary)."""
    totals = {name: 0 for name in OUTCOMES}
    for record in records:
        outcome = outcome_of(record)
        if outcome is not None:
            totals[outcome] += 1
    return totals
