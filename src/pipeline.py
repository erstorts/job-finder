"""Pipeline staleness logic — needs-follow-up and likely-ghosted flags (P3).

These flags are *computed* from the time since a job's last status event, using
thresholds from config — never set by hand (PRD P3). The threshold logic
(:func:`flags_for`) is a pure function of its inputs so it is unit-testable; the
clock is read only in :func:`days_since`, which the UI calls to produce the day
count it passes in.

This module imports no Streamlit (TECH5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def days_since(iso_timestamp: str | None, *, now: datetime | None = None) -> int | None:
    """Whole days between an ISO 8601 timestamp and now.

    Parameters
    ----------
    iso_timestamp:
        The reference timestamp (e.g. the last status event). ``None`` yields
        ``None``.
    now:
        Injectable "current time" for deterministic tests; defaults to
        ``datetime.now()``.

    Returns
    -------
    int or None
        Non-negative whole days elapsed, or ``None`` if the input is missing or
        unparseable.
    """
    if not iso_timestamp:
        return None
    try:
        then = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return None
    now = now or datetime.now()
    return max(0, (now - then).days)


def flags_for(
    days_since_last: int | None,
    is_terminal: bool,
    config: Mapping[str, Any],
) -> dict[str, bool]:
    """Derive the needs-follow-up and likely-ghosted flags (P3).

    Terminal stages (offer/rejected/withdrawn/ghosted) are never flagged — there
    is no follow-up to do. Thresholds come from ``config["followup"]`` (defaults:
    follow-up at 7 days, ghosted at 21).

    Returns
    -------
    dict
        ``{"needs_followup": bool, "likely_ghosted": bool}``.
    """
    cfg = config["followup"]
    if is_terminal or days_since_last is None:
        return {"needs_followup": False, "likely_ghosted": False}

    likely_ghosted = days_since_last >= cfg["ghosted_days"]
    # "Needs follow-up" covers the non-terminal idle window; once it crosses the
    # ghosted threshold we still surface it as needing attention.
    needs_followup = days_since_last >= cfg["followup_days"]
    return {"needs_followup": needs_followup, "likely_ghosted": likely_ghosted}
