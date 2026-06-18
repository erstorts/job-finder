"""Application logging page (PRD L1, L2).

After the user applies on the original site, they record the application labels
here. Saving the labels also appends an ``applied`` status event (L2), sharing
the same timestamp so the pipeline and analytics agree.
"""

from __future__ import annotations

import streamlit as st

from src.db import (
    add_status_event,
    current_status,
    get_application,
    list_pipeline,
    now_iso,
    save_application,
)
from ui_common import get_conn

st.set_page_config(page_title="Log application — JAMS", layout="wide")
st.title("Log an application")

conn = get_conn()

jobs = list_pipeline(conn)
if not jobs:
    st.info("No jobs captured yet. Use the **Triage** page first.")
    st.stop()


def _label(job: dict) -> str:
    return (f"#{job['job_id']} · {job['company_name'] or '—'} — "
            f"{job['title'] or '—'} ({job['current_status']})")


selected = st.selectbox("Job", jobs, format_func=_label)
job_id = selected["job_id"]
existing = get_application(conn, job_id) or {}

if existing:
    st.caption(f"Already logged on {existing.get('applied_at')}. Re-saving updates it.")

with st.form("log_form"):
    applied_via = st.text_input(
        "Applied via", value=existing.get("applied_via") or "",
        help='e.g. "company-site", "linkedin-easy-apply", "referral"',
    )
    c1, c2, c3 = st.columns(3)
    cover_letter = c1.checkbox("Cover letter", value=bool(existing.get("cover_letter")))
    tailored_resume = c2.checkbox(
        "Tailored resume", value=bool(existing.get("tailored_resume"))
    )
    referral = c3.checkbox("Referral", value=bool(existing.get("referral")))

    if st.form_submit_button("Save application"):
        applied_at = existing.get("applied_at") or now_iso()
        save_application(
            conn, job_id,
            applied_at=applied_at,
            applied_via=applied_via or None,
            cover_letter=1 if cover_letter else 0,
            tailored_resume=1 if tailored_resume else 0,
            referral=1 if referral else 0,
        )
        # L2 — recording an application appends an `applied` status event, but
        # only once (don't stack duplicate events when editing labels later).
        if current_status(conn, job_id) != "applied" and not existing:
            add_status_event(conn, job_id, "applied", occurred_at=applied_at)
        st.success("Application logged. Current status is now `applied`.")
        st.rerun()
