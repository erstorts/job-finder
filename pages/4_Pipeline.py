"""Pipeline page — everything you've applied to, with an outcome (P1, P2).

One row per applied job: id, company, title, days idle (since the last update),
and an outcome dropdown. Choosing an outcome **appends** a new status_event —
history is never overwritten — so a job that lands an interview shows up in
Interview Prep and feeds the analytics.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import pipeline
from src.db import add_status_event, list_applied
from ui_common import get_conn

st.set_page_config(page_title="Pipeline — JAMS", layout="wide")
st.title("Pipeline")

conn = get_conn()

# Friendly labels for the outcome dropdown <-> stored status names.
OUTCOME_LABELS = {
    "applied": "Applied (no response yet)",
    "landed_interview": "Landed interview",
    "rejected": "Rejection email",
    "ghosted": "Ghosted",
}
LABEL_TO_STATUS = {v: k for k, v in OUTCOME_LABELS.items()}
OUTCOME_OPTIONS = list(OUTCOME_LABELS.values())

rows = list_applied(conn)
if not rows:
    st.info(
        "No applications yet. Apply to jobs from the **To-do list** and they'll "
        "show up here."
    )
    st.stop()

display = []
for r in rows:
    display.append({
        "job_id": r["job_id"],
        "company": r["company_name"],
        "title": r["title"],
        "days_idle": pipeline.days_since(r["last_event_at"]),
        "outcome": OUTCOME_LABELS.get(r["current_status"], r["current_status"]),
    })

df = pd.DataFrame(display)
original_outcome = dict(zip(df["job_id"], df["outcome"]))

st.caption(
    "Set an outcome to append a new event (history is preserved). 'Days idle' is "
    "days since the last update."
)

edited = st.data_editor(
    df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "job_id": st.column_config.NumberColumn("ID", disabled=True),
        "company": st.column_config.TextColumn("Company", disabled=True),
        "title": st.column_config.TextColumn("Title", disabled=True),
        "days_idle": st.column_config.NumberColumn("Days idle", disabled=True),
        "outcome": st.column_config.SelectboxColumn(
            "Outcome", options=OUTCOME_OPTIONS, required=True,
        ),
    },
    key="pipeline_editor",
)

if st.button("Save outcomes"):
    changed = 0
    for _, row in edited.iterrows():
        job_id = int(row["job_id"])
        new_label = row["outcome"]
        if new_label != original_outcome.get(job_id):
            add_status_event(conn, job_id, LABEL_TO_STATUS[new_label])
            changed += 1
    if changed:
        st.success(f"Appended {changed} outcome event(s).")
        st.rerun()
    else:
        st.info("No outcome changes to save.")
