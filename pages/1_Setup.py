"""Setup page — resume, LinkedIn, and the skill-alias vocabulary.

Thin Streamlit page: it gathers input and delegates all persistence to typed
functions in src/db.py. The resume + LinkedIn text and the controlled alias
vocabulary are the only inputs the ATS keyword match reads, so this page is what
makes triage scores meaningful.

The LLM-assisted "suggest aliases" action only ever writes after the user
confirms — the model never edits the vocabulary at runtime.
"""

from __future__ import annotations

import streamlit as st

from src import extraction as extraction_mod
from src.config import get_config
from src.db import (
    add_skill_alias,
    delete_skill_alias,
    get_profile,
    list_skill_aliases,
    save_profile,
)
from ui_common import get_conn

st.set_page_config(page_title="Setup — JAMS", layout="wide")
st.title("Setup")

conn = get_conn()
config = get_config()

# --------------------------------------------------------------------------
# Profile — resume + LinkedIn text. The ATS match reads both, so "update your
# resume and/or LinkedIn" translates directly into a higher score here.
# --------------------------------------------------------------------------
profile = get_profile(conn) or {}

st.header("Resume & LinkedIn")
st.caption(
    "The ATS score matches a listing's skills against your **resume** only. "
    "Keeping it current is what makes the triage scores — and the 'what to add' "
    "hints — accurate. LinkedIn text is stored for your reference but is not "
    "scored."
)
with st.form("profile_form"):
    resume_text = st.text_area(
        "Resume text", value=profile.get("resume_text") or "", height=240,
        help="Paste your full resume. This is the only text the ATS score reads.",
    )
    linkedin_text = st.text_area(
        "LinkedIn text (stored only — not scored)",
        value=profile.get("linkedin_text") or "", height=180,
        help="Optional. Kept for reference; the ATS score ignores it.",
    )
    if st.form_submit_button("Save profile"):
        save_profile(
            conn,
            resume_text=resume_text or None,
            linkedin_text=linkedin_text or None,
        )
        st.success("Profile saved.")

# Re-read after the form so the sections below (skill extraction) see a
# just-saved profile in the same run rather than the stale top-of-script read.
profile = get_profile(conn) or {}

st.divider()

# --------------------------------------------------------------------------
# Skill alias CRUD — the controlled vocabulary behind deterministic skill
# matching (src/skills.py). Each canonical skill maps to one or more aliases.
# --------------------------------------------------------------------------
st.header("Skill aliases")
st.caption(
    "Each canonical skill maps to one or more surface forms. Matching is a "
    "deterministic lookup against these aliases — not embeddings — so a listing "
    "that says 'Airflow' can count toward a resume that says 'orchestration'."
)

aliases = list_skill_aliases(conn)
if aliases:
    for row in aliases:
        c1, c2, c3 = st.columns([3, 4, 1])
        c1.write(f"**{row['canonical_skill']}**")
        c2.write(row["alias"])
        if c3.button("Delete", key=f"del_alias_{row['id']}"):
            delete_skill_alias(conn, row["id"])
            st.rerun()
else:
    st.info("No skill aliases yet. Add some below.")

with st.form("add_alias_form", clear_on_submit=True):
    ac1, ac2 = st.columns(2)
    new_canonical = ac1.text_input("Canonical skill", placeholder="orchestration")
    new_alias = ac2.text_input("Alias", placeholder="airflow")
    if st.form_submit_button("Add alias"):
        if new_canonical.strip() and new_alias.strip():
            inserted = add_skill_alias(
                conn, new_canonical.strip(), new_alias.strip()
            )
            if inserted:
                st.success(f"Added {new_canonical} → {new_alias}")
            else:
                st.warning("That alias pair already exists.")
            st.rerun()
        else:
            st.error("Both fields are required.")

st.divider()

# --------------------------------------------------------------------------
# LLM extraction — build the vocabulary from the resume + LinkedIn text. Always
# a reviewed step: nothing is written without confirmation, and the model never
# edits the vocabulary directly.
# --------------------------------------------------------------------------
st.header("Extract skills from your resume & LinkedIn")
st.caption(
    "Let the LLM read your saved resume + LinkedIn text and propose the whole "
    "skill vocabulary (canonical skills and the aliases a listing might use). "
    "It's a reviewed step — nothing is written until you confirm."
)

has_profile_text = bool(
    (profile.get("resume_text") or "").strip()
    or (profile.get("linkedin_text") or "").strip()
)

if not has_profile_text:
    st.info("Save your resume (and optionally LinkedIn) text above first, then "
            "the LLM can extract your skills from it.")
elif st.button("Extract skills from resume & LinkedIn"):
    try:
        with st.spinner("Reading your resume & LinkedIn…"):
            result = extraction_mod.extract_skill_aliases(
                profile.get("resume_text"), profile.get("linkedin_text"), config
            )
        st.session_state["alias_suggestions"] = [
            s.model_dump() for s in result.suggestions
        ]
        if not result.suggestions:
            st.warning("No skills were extracted. Check that your resume text is "
                       "saved above.")
    except Exception as exc:  # noqa: BLE001 — surface API/setup errors to the user
        st.error(f"Extraction failed: {exc}. Is ANTHROPIC_API_KEY set?")

pending = st.session_state.get("alias_suggestions")
if pending:
    existing = {(r["canonical_skill"], r["alias"]) for r in aliases}
    fresh = [s for s in pending if (s["canonical_skill"], s["alias"]) not in existing]
    if not fresh:
        st.success("Every extracted alias is already in your vocabulary.")
    else:
        st.write("**Review extracted skills** — check the ones to add:")
        c_all1, c_all2 = st.columns([1, 4])
        if c_all1.button("Select all"):
            for i in range(len(fresh)):
                st.session_state[f"sugg_{i}"] = True
        chosen = []
        for i, s in enumerate(fresh):
            if st.checkbox(
                f"{s['canonical_skill']} → {s['alias']}", key=f"sugg_{i}"
            ):
                chosen.append(s)
        if st.button("Add selected aliases", disabled=not chosen):
            for s in chosen:
                add_skill_alias(conn, s["canonical_skill"], s["alias"])
            st.session_state.pop("alias_suggestions", None)
            st.success(f"Added {len(chosen)} alias(es).")
            st.rerun()
