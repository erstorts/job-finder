"""Interview prep page (I1).

Lists every job that landed an interview (current status ``landed_interview``)
with a button to open the original listing so you can prep against it.
"""

from __future__ import annotations

import streamlit as st

from src.db import get_listings, jobs_in_statuses
from ui_common import get_conn

st.set_page_config(page_title="Interview prep — JAMS", layout="wide")
st.title("Interview prep")

conn = get_conn()

interviews = jobs_in_statuses(conn, ["landed_interview"])
if not interviews:
    st.info(
        "No interviews yet. Mark a job as **Landed interview** on the "
        "**Pipeline** page and it'll show up here to prep."
    )
    st.stop()

st.caption("Jobs where you landed an interview. Open the listing to prep against it.")

for row in interviews:
    job_id = row["job_id"]
    c1, c2 = st.columns([5, 2])
    loc = "Denver" if row.get("location_type") == "denver" else "Remote"
    c1.markdown(f"**{row['company_name']} — {row['title']}**  ·  {loc}")

    # The apply link doubles as the listing link for prep.
    url = row.get("url")
    if not url:
        listings = get_listings(conn, job_id)
        url = next((l["url"] for l in listings if l["url"]), None)
    if url:
        c2.link_button("Open listing ↗", url, use_container_width=True)
    else:
        c2.caption("No listing link")
