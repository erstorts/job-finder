"""JAMS Streamlit entry point — the Dashboard landing page.

Answers "what needs my attention right now?" the moment the app opens: jobs
waiting on the to-do list, interviews to prep, applications going stale, then
headline counts. Surfaced items link to the relevant page. Also ensures the
database exists on startup.

Run with::

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from src import pipeline
from src.config import get_config
from src.db import list_applied, list_todo
from ui_common import _ensure_initialized, get_conn


def main() -> None:
    st.set_page_config(page_title="JAMS — Job Application Tracker", layout="wide")
    _ensure_initialized()
    conn = get_conn()
    config = get_config()

    st.title("Dashboard")

    todo = list_todo(conn)
    applied = list_applied(conn)

    if not todo and not applied:
        st.info("No jobs yet. Head to **Triage** to score your first listing.")
        st.page_link("pages/2_Triage.py", label="→ Triage", icon="🧭")
        return

    interviews = [r for r in applied if r["current_status"] == "landed_interview"]
    ghosted = sum(1 for r in applied if r["current_status"] == "ghosted")

    # ---- Headline counts ----
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("To-do (to apply)", len(todo))
    m2.metric("Applied", len(applied))
    m3.metric("Interviews", len(interviews))
    m4.metric("Ghosted", ghosted)

    st.divider()

    # ---- 1. To-do list ----
    st.subheader(f"📋 To-do — jobs to apply to ({len(todo)})")
    if todo:
        for r in todo[:8]:
            loc = "Denver" if r.get("location_type") == "denver" else "Remote"
            score = r.get("match_score")
            score_str = f"ATS {score:.0f}" if score is not None else "ATS —"
            st.write(f"**{r['company_name']} — {r['title']}** · {loc} · {score_str}")
        st.page_link("pages/3_Log.py", label="→ To-do list", icon="📋")
    else:
        st.caption("Nothing waiting to apply. 🎉")

    # ---- 2. Interviews ----
    st.subheader(f"🎤 Interviews to prep ({len(interviews)})")
    if interviews:
        for r in interviews:
            st.write(f"**{r['company_name']} — {r['title']}**")
        st.page_link("pages/5_Interview_Prep.py", label="→ Interview prep", icon="📝")
    else:
        st.caption("No interviews landed yet.")

    # ---- 3. Stale applications (still 'applied', idle past the follow-up window) ----
    stale = []
    for r in applied:
        if r["current_status"] != "applied":
            continue
        days = pipeline.days_since(r["last_event_at"])
        flags = pipeline.flags_for(days, False, config)
        if flags["needs_followup"]:
            r["_days"] = days
            r["_ghosted"] = flags["likely_ghosted"]
            stale.append(r)
    st.subheader(f"⏰ Going quiet ({len(stale)})")
    if stale:
        for r in sorted(stale, key=lambda x: x["_days"] or 0, reverse=True):
            ghost = " · likely ghosted" if r["_ghosted"] else ""
            st.write(
                f"**{r['company_name']} — {r['title']}** · idle {r['_days']}d{ghost}"
            )
        st.page_link("pages/4_Pipeline.py", label="→ Pipeline", icon="📊")
    else:
        st.caption("No applications going stale.")

    # ---- 4. Recent activity ----
    st.subheader("🕑 Recent activity")
    for r in applied[:8]:  # list_applied is ordered by last_event_at DESC
        st.write(
            f"{r['last_event_at']} · **{r['company_name']} — {r['title']}** · "
            f"{r['current_status']}"
        )


if __name__ == "__main__":
    main()
