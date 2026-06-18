"""Application logging page (PRD L1, L2).

Shows every captured job in a table with the facts pulled at triage, a link to
the original listing (to go apply), and an **Applied** button per row. Clicking
that button opens the application-logging form in a modal popup; saving the
labels also appends an ``applied`` status event (L2), sharing the same timestamp
so the pipeline and analytics agree.
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


def _salary(job: dict) -> str:
    """Render the salary range from whatever bounds were extracted, or a dash."""
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if lo and hi:
        return f"${lo:,}–${hi:,}"
    if lo:
        return f"${lo:,}+"
    if hi:
        return f"up to ${hi:,}"
    return "—"


def _location(job: dict) -> str:
    loc = job.get("location") or "—"
    if job.get("remote_flag"):
        loc = f"{loc} · remote" if loc != "—" else "remote"
    return loc


@st.dialog("Log application")
def log_dialog(job: dict) -> None:
    """Modal form for recording the application labels for a single job (L1)."""
    job_id = job["job_id"]
    existing = get_application(conn, job_id) or {}

    st.markdown(f"**{job['company_name'] or '—'} — {job['title'] or '—'}**")
    if existing:
        st.caption(
            f"Already logged on {existing.get('applied_at')}. Re-saving updates it."
        )

    with st.form("log_form"):
        applied_via = st.text_input(
            "Applied via", value=existing.get("applied_via") or "",
            help='e.g. "company-site", "linkedin-easy-apply", "referral"',
        )
        c1, c2, c3 = st.columns(3)
        cover_letter = c1.checkbox(
            "Cover letter", value=bool(existing.get("cover_letter"))
        )
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
            # L2 — recording an application appends an `applied` status event,
            # but only once (don't stack duplicate events when editing later).
            if current_status(conn, job_id) != "applied" and not existing:
                add_status_event(conn, job_id, "applied", occurred_at=applied_at)
            st.success("Application logged. Current status is now `applied`.")
            st.rerun()


# Column layout shared by the header and every data row.
COLS = [3, 3, 2, 2, 1, 2, 2, 2]
HEADERS = [
    "Company", "Title", "Location", "Salary",
    "Score", "Status", "Listing", "Action",
]

header = st.columns(COLS)
for col, label in zip(header, HEADERS):
    col.markdown(f"**{label}**")
st.divider()

for job in jobs:
    row = st.columns(COLS)
    row[0].write(job["company_name"] or "—")
    row[1].write(job["title"] or "—")
    row[2].write(_location(job))
    row[3].write(_salary(job))
    score = job.get("match_score")
    row[4].write(f"{score:.0f}" if score is not None else "—")
    row[5].write(job.get("current_status") or "—")

    url = job.get("url")
    if url:
        row[6].link_button("Open ↗", url)
    else:
        row[6].write("—")

    applied = current_status(conn, job["job_id"]) == "applied"
    label = "✓ Applied" if applied else "Applied"
    if row[7].button(label, key=f"applied_{job['job_id']}"):
        log_dialog(job)
