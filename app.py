"""JAMS Streamlit entry point — the Dashboard landing page (PRD D1, D2).

Answers "what needs my attention right now?" the moment the app opens, in
priority order: applications needing follow-up, active interviews, recent
activity, then headline funnel metrics (D1). Surfaced items link to the relevant
page (D2). Also ensures the database exists on startup.

Run with::

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from src import pipeline
from src.config import get_config
from src.db import list_pipeline, list_statuses
from ui_common import _ensure_initialized, get_conn

INTERVIEW_STAGES = ["recruiter_screen", "hiring_manager", "onsite"]


def main() -> None:
    st.set_page_config(page_title="JAMS — Job Application Tracker", layout="wide")
    _ensure_initialized()
    conn = get_conn()
    config = get_config()

    st.title("Dashboard")

    rows = list_pipeline(conn)
    if not rows:
        st.info("No jobs yet. Head to **Triage** to capture your first listing.")
        st.page_link("pages/2_Triage.py", label="→ Triage", icon="🧭")
        return

    terminal_map = {s["name"]: bool(s["is_terminal"]) for s in list_statuses(conn)}

    # Decorate each row with computed staleness flags (P3 logic reused here).
    for r in rows:
        days = pipeline.days_since(r["last_event_at"])
        r["days_idle"] = days
        r["flags"] = pipeline.flags_for(
            days, terminal_map.get(r["current_status"], False), config
        )

    # ---- Headline funnel metrics (top, for at-a-glance context) ----
    total = len(rows)
    applied = sum(1 for r in rows if r["current_status"] not in ("found",))
    interviews = [r for r in rows if r["current_status"] in INTERVIEW_STAGES]
    offers = sum(1 for r in rows if r["current_status"] == "offer")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Jobs tracked", total)
    m2.metric("In pipeline (applied+)", applied)
    m3.metric("Active interviews", len(interviews))
    m4.metric("Offers", offers)

    st.divider()

    # ---- 1. Needs follow-up (highest priority) ----
    followups = [r for r in rows if r["flags"]["needs_followup"]]
    st.subheader(f"⏰ Needs follow-up ({len(followups)})")
    if followups:
        for r in sorted(followups, key=lambda x: x["days_idle"], reverse=True):
            ghost = " · likely ghosted" if r["flags"]["likely_ghosted"] else ""
            st.write(
                f"**{r['company_name']} — {r['title']}** · {r['current_status']} · "
                f"idle {r['days_idle']}d{ghost}"
            )
        st.page_link("pages/4_Pipeline.py", label="→ Manage in Pipeline", icon="📋")
    else:
        st.caption("Nothing stale. 🎉")

    # ---- 2. Active interviews ----
    st.subheader(f"🎤 Active interviews ({len(interviews)})")
    if interviews:
        for r in interviews:
            st.write(f"**{r['company_name']} — {r['title']}** · {r['current_status']}")
        st.page_link("pages/5_Interview_Prep.py", label="→ Interview prep", icon="📝")
    else:
        st.caption("No active interviews.")

    # ---- 3. Recent activity ----
    st.subheader("🕑 Recent activity")
    for r in rows[:8]:  # list_pipeline is ordered by last_event_at DESC
        st.write(
            f"{r['last_event_at']} · **{r['company_name']} — {r['title']}** · "
            f"{r['current_status']}"
        )


if __name__ == "__main__":
    main()
