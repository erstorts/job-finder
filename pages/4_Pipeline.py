"""Pipeline page — editable status grid with computed staleness flags (P1–P3).

Shows one row per job with its derived current status, days since the last
event, sources, and the computed needs-follow-up / likely-ghosted flags.
Changing a status in the grid **appends** a new status_event (P2) — history is
never overwritten.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import pipeline
from src.config import get_config
from src.db import add_status_event, list_pipeline, list_statuses
from ui_common import get_conn

st.set_page_config(page_title="Pipeline — JAMS", layout="wide")
st.title("Pipeline")

conn = get_conn()
config = get_config()

statuses = list_statuses(conn)
status_names = [s["name"] for s in statuses]
terminal_map = {s["name"]: bool(s["is_terminal"]) for s in statuses}

rows = list_pipeline(conn)
if not rows:
    st.info("No jobs captured yet. Use the **Triage** page first.")
    st.stop()

# Build the display frame, computing days-since and the P3 flags per row.
display = []
for r in rows:
    days = pipeline.days_since(r["last_event_at"])
    flags = pipeline.flags_for(days, terminal_map.get(r["current_status"], False), config)
    display.append({
        "job_id": r["job_id"],
        "company": r["company_name"],
        "title": r["title"],
        "status": r["current_status"],
        "days_since_update": days,
        "needs_followup": flags["needs_followup"],
        "likely_ghosted": flags["likely_ghosted"],
        "sources": r["sources"] or "",
        "score": r["match_score"],
    })

df = pd.DataFrame(display)
original_status = dict(zip(df["job_id"], df["status"]))

st.caption(
    "Change a status to append a new event (history is preserved). Follow-up and "
    "ghosted flags are computed from days since the last update — they are not "
    f"editable. Thresholds: follow-up {config['followup']['followup_days']}d, "
    f"ghosted {config['followup']['ghosted_days']}d."
)

edited = st.data_editor(
    df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "job_id": st.column_config.NumberColumn("ID", disabled=True),
        "company": st.column_config.TextColumn("Company", disabled=True),
        "title": st.column_config.TextColumn("Title", disabled=True),
        "status": st.column_config.SelectboxColumn(
            "Status", options=status_names, required=True,
        ),
        "days_since_update": st.column_config.NumberColumn(
            "Days idle", disabled=True
        ),
        "needs_followup": st.column_config.CheckboxColumn(
            "Follow up?", disabled=True
        ),
        "likely_ghosted": st.column_config.CheckboxColumn("Ghosted?", disabled=True),
        "sources": st.column_config.TextColumn("Sources", disabled=True),
        "score": st.column_config.NumberColumn("Score", disabled=True),
    },
    key="pipeline_editor",
)

if st.button("Save status changes"):
    changed = 0
    for _, row in edited.iterrows():
        job_id = int(row["job_id"])
        new_status = row["status"]
        if new_status != original_status.get(job_id):
            # P2 — append, never overwrite.
            add_status_event(conn, job_id, new_status)
            changed += 1
    if changed:
        st.success(f"Appended {changed} status event(s).")
        st.rerun()
    else:
        st.info("No status changes to save.")
