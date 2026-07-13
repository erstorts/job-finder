"""To-do list — jobs to apply to (L1, L2).

Shows every pursued-but-not-yet-applied job (status ``found``) with the facts
gathered at triage: Denver/remote, salary, ATS score, whether a cover letter is
an option, how long since it was posted, and the apply link. Each row has an
**Apply** button.

Clicking Apply opens a popup with the single question the flow asks — did you
find someone on LinkedIn to message? Saving records the application, appends an
``applied`` status event, and removes the job from this list (it moves to the
Pipeline).
"""

from __future__ import annotations

import streamlit as st

from src import pipeline
from src.db import (
    add_status_event,
    list_todo,
    now_iso,
    save_application,
)
from ui_common import get_conn

st.set_page_config(page_title="To-do list — JAMS", layout="wide")
st.title("To-do list — jobs to apply to")

conn = get_conn()

jobs = list_todo(conn)
if not jobs:
    st.info(
        "Nothing to apply to yet. Score a listing on the **Triage** page and add "
        "it to your to-do list."
    )
    st.stop()


def _salary(job: dict) -> str:
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if lo and hi:
        return f"${lo:,}–${hi:,}"
    if lo:
        return f"${lo:,}+"
    if hi:
        return f"up to ${hi:,}"
    return "—"


def _location(job: dict) -> str:
    return "Denver" if job.get("location_type") == "denver" else "Remote"


def _posted_ago(job: dict) -> str:
    days = pipeline.days_since(job.get("date_posted"))
    if days is None:
        return "—"
    if days == 0:
        return "today"
    return f"{days}d ago"


@st.dialog("Log application")
def apply_dialog(job: dict) -> None:
    """Modal asking only whether the user found someone on LinkedIn to message."""
    job_id = job["job_id"]
    st.markdown(f"**{job['company_name'] or '—'} — {job['title'] or '—'}**")
    st.caption("Recording this application removes it from your to-do list.")

    with st.form("apply_form"):
        linkedin_contact = st.checkbox(
            "I found someone on LinkedIn to message about this role"
        )
        if st.form_submit_button("Mark as applied"):
            applied_at = now_iso()
            save_application(
                conn, job_id,
                applied_at=applied_at,
                linkedin_contact=1 if linkedin_contact else 0,
            )
            add_status_event(conn, job_id, "applied", occurred_at=applied_at)
            st.rerun()


# Column layout shared by the header and every data row.
COLS = [3, 3, 2, 2, 1, 2, 2, 2, 2]
HEADERS = [
    "Company", "Title", "Location", "Salary", "ATS",
    "Cover letter", "Posted", "Apply link", "Action",
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
    row[5].write("Yes" if job.get("cover_letter_option") else "No")
    row[6].write(_posted_ago(job))

    url = job.get("url")
    if url:
        row[7].link_button("Open ↗", url)
    else:
        row[7].write("—")

    if row[8].button("Applied", key=f"apply_{job['job_id']}"):
        apply_dialog(job)
