"""Interview prep page (PRD I1).

Lists jobs currently in an interview stage and, for each, shows the frozen
captured details — jd_text, company description, salary, benefits — plus the
source listing link(s) for preparation.
"""

from __future__ import annotations

import streamlit as st

from src.db import get_job, get_listings, jobs_in_statuses
from ui_common import get_conn

INTERVIEW_STAGES = ["recruiter_screen", "hiring_manager", "onsite"]

st.set_page_config(page_title="Interview prep — JAMS", layout="wide")
st.title("Interview prep")

conn = get_conn()

interviews = jobs_in_statuses(conn, INTERVIEW_STAGES)
if not interviews:
    st.info("No jobs are currently in an interview stage "
            "(recruiter screen, hiring manager, or onsite).")
    st.stop()

st.caption("Showing the originally captured details (frozen at capture) for each "
           "job currently interviewing.")

for row in interviews:
    job = get_job(conn, row["job_id"])
    header = f"{job['company_name']} — {job['title']}  ·  {row['current_status']}"
    with st.expander(header, expanded=False):
        meta = []
        if job.get("location"):
            meta.append(f"📍 {job['location']}")
        if job.get("remote_flag") is not None:
            meta.append("🏠 Remote" if job["remote_flag"] else "🏢 On-site")
        if job.get("salary_min") or job.get("salary_max"):
            meta.append(f"💰 {job.get('salary_min') or '?'}–{job.get('salary_max') or '?'}")
        if meta:
            st.write("  ·  ".join(meta))

        if job.get("benefits"):
            st.markdown(f"**Benefits:** {job['benefits']}")
        if job.get("company_description"):
            st.markdown("**Company**")
            st.write(job["company_description"])

        st.markdown("**Listing text (frozen at capture)**")
        st.text_area(
            "jd_text", value=job.get("jd_text") or "(none captured)",
            height=200, disabled=True, key=f"jd_{job['id']}", label_visibility="collapsed",
        )

        listings = get_listings(conn, job["id"])
        if listings:
            st.markdown("**Source listing(s)**")
            for l in listings:
                site = l["source_site"] or "source"
                if l["url"]:
                    st.markdown(f"- [{site}]({l['url']}) · seen {l['date_seen']}")
                else:
                    st.markdown(f"- {site} · seen {l['date_seen']} (no URL)")
