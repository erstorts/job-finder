"""Conversion-funnel analytics (PRD §A).

Pure computation over the per-job records assembled by
``db.analytics_dataset`` — per-stage conversion, average time-in-stage, and
segmentation by source, score band, and application label. No SQL, no Streamlit,
no clock (durations come from the event timestamps in the data), so this is
unit-testable in isolation.

A1/A2 are computed here; the A3 caveat (observational, low-n, confounded) is
rendered by the page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

# The forward pipeline used for funnel conversion. Backwards/terminal-off-ramp
# stages (rejected/ghosted/withdrawn) are not part of the linear funnel.
MAIN_PIPELINE = [
    "found", "applied", "recruiter_screen", "hiring_manager", "onsite", "offer",
]

# Stages that count as "reached an interview" for segmentation.
INTERVIEW_STAGES = {"recruiter_screen", "hiring_manager", "onsite", "offer"}


def _reached(record: Mapping[str, Any]) -> set[str]:
    """Set of statuses a job ever held (any event with that status)."""
    return {e["status"] for e in record["events"]}


def funnel_counts(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-stage reached-counts and stage-to-stage conversion (A1).

    For each stage in :data:`MAIN_PIPELINE`, count how many jobs ever reached it.
    ``conversion_from_prev`` is this stage's count over the previous stage's
    count (``None`` for the first stage or when the previous count is zero).
    """
    out: list[dict[str, Any]] = []
    prev_count: int | None = None
    for stage in MAIN_PIPELINE:
        count = sum(1 for r in records if stage in _reached(r))
        conversion = (
            None if prev_count in (None, 0) else round(count / prev_count, 3)
        )
        out.append({"stage": stage, "count": count, "conversion_from_prev": conversion})
        prev_count = count
    return out


def avg_time_in_stage(records: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Average days spent in each stage before moving on (A1).

    For each job, consecutive events define a duration attributed to the earlier
    event's status. Durations are averaged per status. Stages a job never left
    (its current status) contribute nothing — there is no "next" event yet.
    """
    totals: dict[str, list[float]] = {}
    for record in records:
        events = sorted(record["events"], key=lambda e: e["occurred_at"])
        for current, nxt in zip(events, events[1:]):
            try:
                t0 = datetime.fromisoformat(current["occurred_at"])
                t1 = datetime.fromisoformat(nxt["occurred_at"])
            except ValueError:
                continue
            days = max(0.0, (t1 - t0).total_seconds() / 86400.0)
            totals.setdefault(current["status"], []).append(days)
    return {
        stage: round(sum(vals) / len(vals), 2)
        for stage, vals in totals.items()
    }


def score_band(score: float | None) -> str:
    """Bucket a match score into a coarse band for segmentation (A2)."""
    if score is None:
        return "unknown"
    if score >= 80:
        return "high (80+)"
    if score >= 60:
        return "medium (60-79)"
    return "low (<60)"


def _segmenters() -> dict[str, Callable[[Mapping[str, Any]], list[str]]]:
    """Map a segmentation name to a function yielding a record's segment keys.

    Source returns one key per distinct source (a multi-source job counts in
    each); the rest return a single key.
    """
    return {
        "source": lambda r: r["sources"] or ["(unknown)"],
        "score_band": lambda r: [score_band(r["match_score"])],
        "cover_letter": lambda r: ["yes" if r["labels"]["cover_letter"] else "no"],
        "tailored_resume": lambda r: ["yes" if r["labels"]["tailored_resume"] else "no"],
        "referral": lambda r: ["yes" if r["labels"]["referral"] else "no"],
    }


def segment_funnel(
    records: Sequence[Mapping[str, Any]], by: str
) -> list[dict[str, Any]]:
    """Conversion segmented by source / score band / application label (A2).

    For each segment, among jobs that ever ``applied``: the fraction that reached
    any interview stage and the fraction that reached an ``offer``. Returns rows
    sorted by applied-count descending.

    Raises
    ------
    KeyError
        If ``by`` is not a known segmentation.
    """
    segmenter = _segmenters()[by]
    # segment -> counters
    buckets: dict[str, dict[str, int]] = {}
    for record in records:
        reached = _reached(record)
        if "applied" not in reached:
            continue  # conversion is conditioned on having applied
        reached_interview = bool(reached & INTERVIEW_STAGES)
        reached_offer = "offer" in reached
        for key in segmenter(record):
            b = buckets.setdefault(key, {"applied": 0, "interviews": 0, "offers": 0})
            b["applied"] += 1
            b["interviews"] += int(reached_interview)
            b["offers"] += int(reached_offer)

    rows = []
    for segment, b in buckets.items():
        applied = b["applied"]
        rows.append({
            "segment": segment,
            "applied": applied,
            "interviews": b["interviews"],
            "offers": b["offers"],
            "interview_rate": round(b["interviews"] / applied, 3) if applied else 0.0,
            "offer_rate": round(b["offers"] / applied, 3) if applied else 0.0,
        })
    rows.sort(key=lambda r: r["applied"], reverse=True)
    return rows
