"""Analytics page — what correlates with landing interviews (§A).

For every applied job we derive one outcome (interview / rejected / ghosted /
still pending) and compare it across each input the user tracks: source, Denver
vs remote, min salary, ATS score, days since posted, cover-letter option, and
whether they found someone on LinkedIn. All math lives in src/analytics.py; this
page only fetches and renders. The observational caveat is shown prominently —
the data is directional, not causal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import analytics
from src.db import analytics_dataset
from ui_common import get_conn

st.set_page_config(page_title="Analytics — JAMS", layout="wide")
st.title("What's working")

conn = get_conn()
records = analytics_dataset(conn)

if not records:
    st.info("No jobs captured yet. Use the **Triage** page first.")
    st.stop()

totals = analytics.outcome_totals(records)
applied_n = sum(totals.values())

if applied_n == 0:
    st.info(
        "No applications yet. Apply to jobs from the **To-do list**, record "
        "outcomes on the **Pipeline** page, and this page will compare them."
    )
    st.stop()

# The load-bearing caveat. Shown before any numbers.
st.warning(
    "**Read this first.** This data is observational, low-n, and confounded, so "
    "it is **directional, not causal**. Don't over-read small uncontrolled "
    "differences — a handful of applications can't tell you what *caused* an "
    "interview."
)

# ---- Headline outcome mix ----
st.subheader("Outcomes so far")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Applied", applied_n)
m2.metric("Interviews", totals["interview"])
m3.metric("Rejected", totals["rejected"])
m4.metric("Ghosted", totals["ghosted"])
if totals["pending"]:
    st.caption(f"{totals['pending']} still pending (applied, no outcome recorded yet).")

st.divider()

# ---- One comparison table per independent variable ----
st.subheader("Interview rate by…")
st.caption(
    "Each table splits your applied jobs by one variable and shows how the "
    "outcomes break down. 'Applied' is the n for that row — read rates with the "
    "low-n caveat above."
)

for key, label in analytics.VARIABLES:
    rows = analytics.segment_outcomes(records, key)
    st.markdown(f"**{label}**")
    if not rows:
        st.caption("No data.")
        continue
    seg_df = pd.DataFrame(rows).rename(columns={
        "segment": label,
        "applied": "Applied",
        "interview": "Interviews",
        "rejected": "Rejected",
        "ghosted": "Ghosted",
        "pending": "Pending",
        "interview_rate": "Interview rate",
    })
    st.dataframe(seg_df, hide_index=True, use_container_width=True)
