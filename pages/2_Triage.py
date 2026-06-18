"""Triage page — Apply / Pass (PRD T1–T6).

In M3 this is the *manual-entry* path: the user types the extraction fields
directly instead of pasting raw text (the LLM extraction in M4 fills the same
fields automatically). Everything downstream — dedup (T3), scoring (T4),
breakdown display (T5), and the decision (T6) — is identical regardless of how
the fields were populated, which is exactly why the PRD builds the deterministic
core before the LLM.

The page is thin: it gathers input and calls the pure functions in src/.
"""

from __future__ import annotations

import json

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
st.title("Triage — Apply / Pass")

conn = get_conn()
config = get_config()
seniority_bands = config["seniority"]["bands"]

profile = get_profile(conn)
if not profile or not profile.get("resume_text"):
    st.warning(
        "No resume saved yet. Skill matching needs your resume — fill it in on "
        "the **Setup** page first for meaningful scores."
    )


def _lines_to_list(text: str) -> list[str]:
    """Split a textarea (comma- or newline-separated) into a clean list."""
    parts = text.replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _tristate(label: str, key: str) -> bool | None:
    """A Yes/No/Unknown selector returning True/False/None."""
    choice = st.selectbox(label, ["Unknown", "Yes", "No"], key=key)
    return {"Unknown": None, "Yes": True, "No": False}[choice]


def _persist_new_job(capture: dict) -> int:
    """Create job + first listing + 'found' event, then score it (T3/T4).

    Returns the new job id. Scoring reads the just-frozen extraction so the
    stored score matches what the user sees.
    """
    extraction = capture["extraction"]
    job_id = insert_job(
        conn,
        company_name=extraction.get("company_name"),
        title=extraction.get("title"),
        location=extraction.get("location"),
        remote_flag=_bool_to_int(extraction.get("remote_flag")),
        jd_text=capture.get("jd_text"),
        salary_min=extraction.get("salary_min"),
        salary_max=extraction.get("salary_max"),
        benefits=extraction.get("benefits"),
        company_description=extraction.get("company_description"),
        extracted_json=json.dumps(extraction),
    )
    add_listing(
        conn, job_id,
        source_site=capture.get("source_site"),
        url=capture.get("url"),
        raw_title=extraction.get("title"),
    )
    add_status_event(conn, job_id, "found")

    result = scoring.score_job(extraction, profile or {}, list_skill_aliases(conn), config)
    set_score(conn, job_id, result.score, json.dumps(result.breakdown), result.rubric_version)
    return job_id


def _bool_to_int(value: bool | None) -> int | None:
    return None if value is None else (1 if value else 0)


def _render_breakdown(job_id: int) -> None:
    """Show the recommendation and the full breakdown beneath it (T5).

    The number is never shown alone: gates, matched/missed skills, and each
    weighted sub-score are always displayed.
    """
    job = get_job(conn, job_id)
    breakdown = json.loads(job["match_breakdown"])
    score = job["match_score"]
    threshold = breakdown["apply_threshold"]

    rec = breakdown["recommendation"]
    header = f"Recommendation: **{rec.upper()}**  —  score {score:.1f} / 100 (threshold {threshold})"
    (st.success if rec == "apply" else st.error)(header)

    if breakdown["gate_failed"]:
        st.warning("A hard gate failed, so the recommendation is forced to PASS.")
    for g in breakdown["gates"]:
        icon = "✅" if g["passed"] else "⛔"
        st.write(f"{icon} **{g['name']} gate** — {g['detail']}")

    st.subheader("Weighted sub-scores")
    weights = breakdown["weights"]
    st.table([
        {
            "criterion": name,
            "sub_score": round(value, 3),
            "weight": weights.get(name, ""),
            "contribution": round(value * weights.get(name, 0) * 100, 2),
        }
        for name, value in breakdown["sub_scores"].items()
    ])

    c1, c2 = st.columns(2)
    c1.write(f"**Matched required:** {breakdown['matched_required'] or '—'}")
    c1.write(f"**Missed required:** {breakdown['missed_required'] or '—'}")
    c2.write(f"**Matched preferred:** {breakdown['matched_preferred'] or '—'}")
    c2.write(f"**Missed preferred:** {breakdown['missed_preferred'] or '—'}")


def _route_capture(capture: dict) -> None:
    """Run dedup (T3) and set the next stage in session_state.

    Shared by the manual-entry and pasted-text paths so both resolve identically:
    confident duplicate attaches a listing (G1), borderline asks the user, a new
    job is created + scored. Ends by triggering a rerun.
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


stage = st.session_state.get("triage_stage", "input")

# --------------------------------------------------------------------------
# Stage: input — gather the listing facts (manual entry in M3).
# --------------------------------------------------------------------------
if stage == "input":
    mode = st.radio(
        "Capture mode",
        ["Paste listing text (LLM extract)", "Manual entry"],
        horizontal=True,
        help="Both paths feed the same deterministic dedup + scoring (T3–T6).",
    )

    # ---- T1/T2: pasted-text path. The LLM extracts; the URL is never fetched.
    if mode == "Paste listing text (LLM extract)":
        with st.form("paste_form"):
            jd_text = st.text_area(
                "Paste the full listing text", height=300,
                help="Pasted text is the input of record. The URL is reference "
                     "only and is never scraped (T1, N3).",
            )
            pc1, pc2 = st.columns(2)
            source_site = pc1.text_input("Source site", placeholder="linkedin")
            url = pc2.text_input("URL (reference only)")
            paste_submitted = st.form_submit_button("Extract & run triage")

        if paste_submitted:
            if not jd_text.strip():
                st.error("Paste the listing text first.")
            else:
                try:
                    with st.spinner("Extracting with the LLM…"):
                        extracted = extraction_mod.extract_job(jd_text, config)
                except ValueError as exc:
                    st.error(f"Extraction failed: {exc}")  # X1: no partial write
                except Exception as exc:  # noqa: BLE001 — surface API/setup errors
                    st.error(
                        f"LLM call failed: {exc}. Is ANTHROPIC_API_KEY set?"
                    )
                else:
                    _route_capture({
                        "extraction": extracted.model_dump(),
                        "jd_text": jd_text,
                        "source_site": source_site or None,
                        "url": url or None,
                    })
        st.stop()

    # ---- Manual-entry path (M3). Same downstream flow.
    with st.form("triage_form"):
        st.caption("Manual entry — type the extraction fields directly.")
        c1, c2, c3 = st.columns(3)
        company_name = c1.text_input("Company name")
        title = c2.text_input("Job title")
        location = c3.text_input("Location")

        c4, c5, c6 = st.columns(3)
        with c4:
            remote_flag = _tristate("Remote?", "in_remote")
        with c5:
            seniority_choice = st.selectbox(
                "Seniority", ["(unknown)"] + seniority_bands
            )
        with c6:
            degree_required = _tristate("Degree required?", "in_degree")

        c7, c8 = st.columns(2)
        salary_min = c7.number_input("Salary min (0 = unknown)", min_value=0, step=5000)
        salary_max = c8.number_input("Salary max (0 = unknown)", min_value=0, step=5000)

        required_skills = st.text_area("Required skills (comma/newline separated)")
        preferred_skills = st.text_area("Preferred skills (comma/newline separated)")
        company_types = st.text_input("Company types (comma-separated)")
        hard_constraints = st.text_input("Hard constraints (comma-separated)")
        min_years = st.number_input(
            "Min years (captured, not scored in v1)", min_value=0, step=1
        )

        benefits = st.text_input("Benefits")
        company_description = st.text_area("Company description")
        jd_text = st.text_area("Full listing text (jd_text — the input of record)", height=150)

        c9, c10 = st.columns(2)
        source_site = c9.text_input("Source site", placeholder="linkedin")
        url = c10.text_input("URL (reference only — never scraped)")

        submitted = st.form_submit_button("Run triage")

    if submitted:
        extraction = {
            "company_name": company_name or None,
            "title": title or None,
            "location": location or None,
            "remote_flag": remote_flag,
            "salary_min": int(salary_min) or None,
            "salary_max": int(salary_max) or None,
            "benefits": benefits or None,
            "company_description": company_description or None,
            "company_types": _lines_to_list(company_types),
            "required_skills": _lines_to_list(required_skills),
            "preferred_skills": _lines_to_list(preferred_skills),
            "min_years": int(min_years) or None,
            "degree_required": degree_required,
            "seniority": None if seniority_choice == "(unknown)" else seniority_choice,
            "hard_constraints": _lines_to_list(hard_constraints),
        }
        _route_capture({
            "extraction": extraction,
            "jd_text": jd_text or None,
            "source_site": source_site or None,
            "url": url or None,
        })

# --------------------------------------------------------------------------
# Stage: duplicate — confident duplicate, already on file (T3, G1).
# --------------------------------------------------------------------------
elif stage == "duplicate":
    job = get_job(conn, st.session_state["candidate_id"])
    status = current_status(conn, job["id"])
    st.info(
        f"Already logged: **{job['company_name']} — {job['title']}**. "
        f"Current status: **{status}**. A new sighting was attached to the "
        "existing job (no duplicate created)."
    )
    st.caption(st.session_state.get("dedup_reason", ""))
    st.button("New capture", on_click=_reset)

# --------------------------------------------------------------------------
# Stage: borderline — let the user confirm the merge or mark distinct (T3).
# --------------------------------------------------------------------------
elif stage == "borderline":
    job = get_job(conn, st.session_state["candidate_id"])
    st.warning("Possible duplicate — please decide.")
    st.caption(st.session_state.get("dedup_reason", ""))
    st.write(f"**Existing job:** {job['company_name']} — {job['title']} "
             f"({current_status(conn, job['id'])})")
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
# Stage: decide — show the breakdown and record apply/pass (T4–T6).
# --------------------------------------------------------------------------
elif stage == "decide":
    job_id = st.session_state["active_job_id"]
    _render_breakdown(job_id)
    job = get_job(conn, job_id)

    if job["decision"]:
        st.success(f"Decision recorded: **{job['decision']}** at {job['decided_at']}.")
        st.button("New capture", on_click=_reset)
    else:
        st.divider()
        c1, c2 = st.columns(2)
        if c1.button("✅ Apply", use_container_width=True):
            set_decision(conn, job_id, "apply")
            st.rerun()
        if c2.button("🚫 Pass", use_container_width=True):
            set_decision(conn, job_id, "pass")
            st.rerun()
