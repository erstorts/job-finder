"""Triage page — ATS evaluation, then pursue or pass.

Instead of a hard apply/pass verdict, triage scores how well your resume +
LinkedIn already cover a listing's skills (an ATS-style keyword match) and shows
exactly which keywords are missing — so you can decide whether to update your
resume/LinkedIn, add it to your to-do list as-is, or pass.

Here you also flag the three things the rest of the app tracks: Denver-local vs
remote, whether the application offers a cover letter, and how long ago the
listing was posted.

The page is thin: it gathers input and calls the pure functions in src/.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import streamlit as st

from src import dedup, extraction as extraction_mod, scoring
from src.config import get_config
from src.db import (
    add_listing,
    add_status_event,
    current_status,
    get_dedup_candidates,
    get_job,
    get_profile,
    insert_job,
    list_skill_aliases,
    set_decision,
    set_score,
)
from ui_common import get_conn

st.set_page_config(page_title="Triage — JAMS", layout="wide")
st.title("Triage — ATS evaluation")

conn = get_conn()
config = get_config()

profile = get_profile(conn)
if not profile or not profile.get("resume_text"):
    st.warning(
        "No resume saved yet. The ATS match reads your resume — add it on the "
        "**Setup** page first for meaningful scores."
    )

LOCATION_OPTIONS = ["Denver (local)", "Remote"]
LOCATION_VALUES = {"Denver (local)": "denver", "Remote": "remote"}


def _lines_to_list(text: str) -> list[str]:
    """Split a textarea (comma- or newline-separated) into a clean list."""
    parts = text.replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _date_posted(days_ago: int) -> str:
    """ISO date for a listing posted ``days_ago`` days before today."""
    return (date.today() - timedelta(days=max(0, days_ago))).isoformat()


def _persist_new_job(capture: dict) -> int:
    """Create the job + first listing and score it. No status event yet.

    The found/passed status event is appended at the decide stage, so a job the
    user abandons mid-triage never lands on the to-do list.
    """
    extraction = capture["extraction"]
    job_id = insert_job(
        conn,
        company_name=extraction.get("company_name"),
        title=extraction.get("title"),
        location_type=capture["location_type"],
        jd_text=capture.get("jd_text"),
        salary_min=extraction.get("salary_min"),
        salary_max=extraction.get("salary_max"),
        cover_letter_option=capture["cover_letter_option"],
        date_posted=capture["date_posted"],
        extracted_json=json.dumps(extraction),
    )
    add_listing(
        conn, job_id,
        source_site=capture.get("source_site"),
        url=capture.get("url"),
        raw_title=extraction.get("title"),
    )
    result = scoring.score_job(
        extraction, profile or {}, list_skill_aliases(conn), config
    )
    set_score(conn, job_id, result.score, json.dumps(result.breakdown), result.rubric_version)
    return job_id


def _render_ats(job_id: int) -> None:
    """Show the ATS score, the band, and the missing keywords (what to add)."""
    job = get_job(conn, job_id)
    breakdown = json.loads(job["match_breakdown"])
    score = job["match_score"] or 0.0
    band = breakdown["band"]

    label = {"strong": "Strong match", "moderate": "Moderate match", "weak": "Weak match"}[band]
    banner = f"ATS score: **{score:.0f} / 100** — {label}"
    {"strong": st.success, "moderate": st.warning, "weak": st.error}[band](banner)

    if not breakdown["scorable"]:
        st.info(
            "No required/preferred skills were extracted from this listing, so "
            "there's nothing to match on. In manual mode you can type the skills "
            "in yourself to get a score."
        )

    missing = breakdown["missing_keywords"]
    if missing:
        st.markdown(
            "**Missing keywords** — add these to your resume to raise the score "
            "before applying:"
        )
        st.write(", ".join(missing))
    elif breakdown["scorable"]:
        st.caption("✅ Your resume already covers every listed skill.")

    c1, c2 = st.columns(2)
    c1.write(f"**Required covered:** {breakdown['required_coverage'] * 100:.0f}%")
    c1.write(f"Matched: {breakdown['matched_required'] or '—'}")
    c1.write(f"Missing: {breakdown['missed_required'] or '—'}")
    c2.write(f"**Preferred covered:** {breakdown['preferred_coverage'] * 100:.0f}%")
    c2.write(f"Matched: {breakdown['matched_preferred'] or '—'}")
    c2.write(f"Missing: {breakdown['missed_preferred'] or '—'}")


def _render_facts(job: dict) -> None:
    """Compact recap of the captured facts and triage flags."""
    loc = "Denver (local)" if job.get("location_type") == "denver" else "Remote"
    cl = "yes" if job.get("cover_letter_option") else "no"
    lo, hi = job.get("salary_min"), job.get("salary_max")
    sal = f"${lo:,}–${hi:,}" if lo and hi else (f"${lo:,}+" if lo else (f"up to ${hi:,}" if hi else "—"))
    st.caption(
        f"📍 {loc}  ·  💰 {sal}  ·  ✉️ cover letter: {cl}  ·  🗓️ posted {job.get('date_posted') or '—'}"
    )


def _route_capture(capture: dict) -> None:
    """Run dedup and set the next stage in session_state.

    Confident duplicate attaches a listing; borderline asks the user; a new job
    is created + scored. Ends by triggering a rerun.
    """
    extraction = capture["extraction"]
    st.session_state["capture"] = capture
    result = dedup.find_duplicate(extraction, get_dedup_candidates(conn), config)

    if result.status == "confident_duplicate":
        add_listing(
            conn, result.job_id,
            source_site=capture["source_site"], url=capture["url"],
            raw_title=extraction.get("title"),
        )
        st.session_state["triage_stage"] = "duplicate"
        st.session_state["candidate_id"] = result.job_id
        st.session_state["dedup_reason"] = result.reason
    elif result.status == "borderline":
        st.session_state["triage_stage"] = "borderline"
        st.session_state["candidate_id"] = result.job_id
        st.session_state["dedup_reason"] = result.reason
    else:  # new_job
        st.session_state["active_job_id"] = _persist_new_job(capture)
        st.session_state["triage_stage"] = "decide"
    st.rerun()


def _reset() -> None:
    for key in (
        "triage_stage", "capture", "active_job_id", "candidate_id", "dedup_reason",
    ):
        st.session_state.pop(key, None)


def _flag_inputs():
    """Shared triage-flag widgets. Returns (location_type, cover_letter, days_ago)."""
    f1, f2, f3 = st.columns(3)
    loc_label = f1.radio("Location", LOCATION_OPTIONS, horizontal=False)
    cover_letter = f2.checkbox("Cover letter is an option")
    days_ago = f3.number_input("Posted how many days ago?", min_value=0, step=1, value=0)
    return LOCATION_VALUES[loc_label], (1 if cover_letter else 0), int(days_ago)


stage = st.session_state.get("triage_stage", "input")

# --------------------------------------------------------------------------
# Stage: input — gather the listing facts + triage flags.
# --------------------------------------------------------------------------
if stage == "input":
    mode = st.radio(
        "Capture mode",
        ["Paste listing text (LLM extract)", "Manual entry"],
        horizontal=True,
        help="Both paths feed the same ATS scoring.",
    )

    if mode == "Paste listing text (LLM extract)":
        with st.form("paste_form"):
            jd_text = st.text_area(
                "Paste the full listing text", height=280,
                help="The pasted text is the input of record. The URL is the "
                     "apply link and is never scraped.",
            )
            pc1, pc2 = st.columns(2)
            source_site = pc1.text_input("Source site", placeholder="linkedin")
            url = pc2.text_input("Apply link (URL)")
            location_type, cover_letter, days_ago = _flag_inputs()
            paste_submitted = st.form_submit_button("Extract & score")

        if paste_submitted:
            if not jd_text.strip():
                st.error("Paste the listing text first.")
            else:
                try:
                    with st.spinner("Extracting with the LLM…"):
                        extracted = extraction_mod.extract_job(jd_text, config)
                except ValueError as exc:
                    st.error(f"Extraction failed: {exc}")
                except Exception as exc:  # noqa: BLE001 — surface API/setup errors
                    st.error(f"LLM call failed: {exc}. Is ANTHROPIC_API_KEY set?")
                else:
                    _route_capture({
                        "extraction": extracted.model_dump(),
                        "jd_text": jd_text,
                        "source_site": source_site or None,
                        "url": url or None,
                        "location_type": location_type,
                        "cover_letter_option": cover_letter,
                        "date_posted": _date_posted(days_ago),
                    })
        st.stop()

    # ---- Manual-entry path. Same downstream flow.
    with st.form("triage_form"):
        st.caption("Manual entry — type the fields directly.")
        c1, c2 = st.columns(2)
        company_name = c1.text_input("Company name")
        title = c2.text_input("Job title")

        c3, c4 = st.columns(2)
        salary_min = c3.number_input("Salary min (0 = unknown)", min_value=0, step=5000)
        salary_max = c4.number_input("Salary max (0 = unknown)", min_value=0, step=5000)

        required_skills = st.text_area("Required skills (comma/newline separated)")
        preferred_skills = st.text_area("Preferred skills (comma/newline separated)")
        jd_text = st.text_area("Full listing text (optional — for interview prep)", height=120)

        c5, c6 = st.columns(2)
        source_site = c5.text_input("Source site", placeholder="linkedin")
        url = c6.text_input("Apply link (URL)")
        location_type, cover_letter, days_ago = _flag_inputs()

        submitted = st.form_submit_button("Score this listing")

    if submitted:
        extraction = {
            "company_name": company_name or None,
            "title": title or None,
            "salary_min": int(salary_min) or None,
            "salary_max": int(salary_max) or None,
            "required_skills": _lines_to_list(required_skills),
            "preferred_skills": _lines_to_list(preferred_skills),
        }
        _route_capture({
            "extraction": extraction,
            "jd_text": jd_text or None,
            "source_site": source_site or None,
            "url": url or None,
            "location_type": location_type,
            "cover_letter_option": cover_letter,
            "date_posted": _date_posted(days_ago),
        })

# --------------------------------------------------------------------------
# Stage: duplicate — confident duplicate, already on file.
# --------------------------------------------------------------------------
elif stage == "duplicate":
    job = get_job(conn, st.session_state["candidate_id"])
    status = current_status(conn, job["id"])
    st.info(
        f"Already logged: **{job['company_name']} — {job['title']}**. "
        f"Current status: **{status or 'undecided'}**. A new sighting was "
        "attached to the existing job (no duplicate created)."
    )
    st.caption(st.session_state.get("dedup_reason", ""))
    st.button("New capture", on_click=_reset)

# --------------------------------------------------------------------------
# Stage: borderline — let the user confirm the merge or mark distinct.
# --------------------------------------------------------------------------
elif stage == "borderline":
    job = get_job(conn, st.session_state["candidate_id"])
    st.warning("Possible duplicate — please decide.")
    st.caption(st.session_state.get("dedup_reason", ""))
    st.write(f"**Existing job:** {job['company_name']} — {job['title']} "
             f"({current_status(conn, job['id']) or 'undecided'})")
    new_title = st.session_state["capture"]["extraction"].get("title")
    st.write(f"**New listing title:** {new_title}")

    c1, c2 = st.columns(2)
    if c1.button("Same job — attach listing"):
        cap = st.session_state["capture"]
        add_listing(
            conn, job["id"],
            source_site=cap["source_site"], url=cap["url"],
            raw_title=new_title,
        )
        st.session_state["triage_stage"] = "duplicate"
        st.rerun()
    if c2.button("Distinct job — create new"):
        st.session_state["active_job_id"] = _persist_new_job(st.session_state["capture"])
        st.session_state["triage_stage"] = "decide"
        st.rerun()

# --------------------------------------------------------------------------
# Stage: decide — show the ATS breakdown and record pursue/pass.
# --------------------------------------------------------------------------
elif stage == "decide":
    job_id = st.session_state["active_job_id"]
    job = get_job(conn, job_id)
    st.subheader(f"{job['company_name'] or '—'} — {job['title'] or '—'}")
    _render_facts(job)
    st.divider()
    _render_ats(job_id)

    if job["decision"]:
        if job["decision"] == "pursue":
            st.success("Added to your **to-do list**. Head to **Log** to apply.")
            st.page_link("pages/3_Log.py", label="→ To-do list", icon="📋")
        else:
            st.info("Marked as **passed**.")
        st.button("New capture", on_click=_reset)
    else:
        st.divider()
        st.caption(
            "Add it to your to-do list (apply as-is, or after updating your "
            "resume), or pass."
        )
        c1, c2 = st.columns(2)
        if c1.button("✅ Add to to-do list", use_container_width=True):
            set_decision(conn, job_id, "pursue")
            add_status_event(conn, job_id, "found")
            st.rerun()
        if c2.button("🚫 Pass", use_container_width=True):
            set_decision(conn, job_id, "pass")
            add_status_event(conn, job_id, "passed")
            st.rerun()
