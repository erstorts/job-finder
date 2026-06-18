"""Analytics page — the conversion funnel (PRD A1–A3).

Renders per-stage conversion and average time-in-stage from the status-event
log (A1), with segmentation by source, score band, and application label (A2).
All math lives in src/analytics.py; this page only fetches and renders. The A3
observational caveat is shown prominently — the data is directional, not causal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src import analytics
from src.db import analytics_dataset
from ui_common import get_conn

st.set_page_config(page_title="Analytics — JAMS", layout="wide")
st.title("Conversion funnel")

conn = get_conn()
records = analytics_dataset(conn)

if not records:
    st.info("No jobs captured yet. Use the **Triage** page first.")
    st.stop()

# A3 — the load-bearing caveat. Shown before any numbers.
st.warning(
    "**Read this first.** This data is observational, low-n, and confounded, so "
    "it is **directional, not causal**. For example, cover letters tend to be "
    "written for better-fit jobs, so a cover-letter \"lift\" may just be a fit "
    "effect. Don't over-read small uncontrolled differences."
)

# ---- A1: per-stage funnel + conversion ----
st.subheader("Pipeline funnel")
funnel = analytics.funnel_counts(records)
funnel_df = pd.DataFrame(funnel)
st.bar_chart(funnel_df.set_index("stage")["count"])
st.dataframe(
    funnel_df.rename(columns={
        "stage": "Stage", "count": "Reached", "conversion_from_prev": "Conv. from prev",
    }),
    hide_index=True, use_container_width=True,
)

# ---- A1: average time-in-stage ----
st.subheader("Average time in stage (days)")
time_in_stage = analytics.avg_time_in_stage(records)
if time_in_stage:
    # Order by the canonical pipeline for readability.
    ordered = {s: time_in_stage[s] for s in analytics.MAIN_PIPELINE if s in time_in_stage}
    extra = {s: v for s, v in time_in_stage.items() if s not in ordered}
    tis = {**ordered, **extra}
    st.bar_chart(pd.Series(tis, name="avg_days"))
else:
    st.caption("Not enough stage transitions yet to compute time-in-stage.")

# ---- A2: segmentation ----
st.subheader("Segmented conversion (among jobs that applied)")
seg_choice = st.selectbox(
    "Segment by",
    ["source", "score_band", "cover_letter", "tailored_resume", "referral"],
)
seg_rows = analytics.segment_funnel(records, seg_choice)
if seg_rows:
    seg_df = pd.DataFrame(seg_rows).rename(columns={
        "segment": seg_choice, "applied": "Applied", "interviews": "Interviews",
        "offers": "Offers", "interview_rate": "Interview rate", "offer_rate": "Offer rate",
    })
    st.dataframe(seg_df, hide_index=True, use_container_width=True)
    st.caption(
        f"n per segment is shown as **Applied** — interpret rates with the "
        "low-n caveat above."
    )
else:
    st.caption("No applications logged yet, so there is nothing to segment.")
