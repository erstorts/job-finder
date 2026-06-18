"""Setup page — profile, target, and skill aliases (PRD S1–S4).

Thin Streamlit page: it gathers input and delegates all persistence to typed
functions in src/db.py (TECH5). The LLM-assisted "suggest aliases" action (S4)
is stubbed here in M2 and wired to the real extraction client in M4 — and it
only ever writes after the user confirms (the model never edits the vocabulary
at runtime).
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
from ui_common import (
    get_conn,
    json_list_to_text,
    text_to_json_list,
    to_int_or_none,
)

st.set_page_config(page_title="Setup — JAMS", layout="wide")
st.title("Setup")

conn = get_conn()
config = get_config()
seniority_bands = config["seniority"]["bands"]

# --------------------------------------------------------------------------
# S1 + S2 — Profile and target. Persisted to the single profile row.
# --------------------------------------------------------------------------
profile = get_profile(conn) or {}

st.header("Profile & Target")
with st.form("profile_form"):
    st.subheader("Resume & LinkedIn (S1)")
    resume_text = st.text_area(
        "Resume text", value=profile.get("resume_text") or "", height=200,
        help="Paste your resume. Skill matching reads this text.",
    )
    linkedin_text = st.text_area(
        "LinkedIn text", value=profile.get("linkedin_text") or "", height=150,
    )

    st.subheader("Target (S2)")
    target_description = st.text_area(
        "Target description (free text)",
        value=profile.get("target_description") or "",
        help='e.g. "early-stage SaaS startups doing data infra"',
    )
    col1, col2 = st.columns(2)
    with col1:
        target_company_types = st.text_input(
            "Target company types (comma-separated)",
            value=json_list_to_text(profile.get("target_company_types")),
            help='e.g. "saas, startup"',
        )
        # Seniority is an ordinal band from the shared config vocabulary.
        current_seniority = profile.get("target_seniority")
        seniority_index = (
            seniority_bands.index(current_seniority)
            if current_seniority in seniority_bands
            else 0
        )
        target_seniority = st.selectbox(
            "Target seniority band", options=seniority_bands, index=seniority_index,
        )
        target_locations = st.text_input(
            "Acceptable locations (comma-separated)",
            value=json_list_to_text(profile.get("target_locations")),
            help='e.g. "Remote, New York, Boston"',
        )
    with col2:
        target_min_comp = st.number_input(
            "Comp floor (annual, 0 = unset)",
            min_value=0, step=5000,
            value=int(profile.get("target_min_comp") or 0),
        )
        target_remote_ok = st.checkbox(
            "Remote OK", value=bool(profile.get("target_remote_ok")),
        )

    submitted = st.form_submit_button("Save profile")
    if submitted:
        save_profile(
            conn,
            resume_text=resume_text or None,
            linkedin_text=linkedin_text or None,
            target_description=target_description or None,
            target_company_types=text_to_json_list(target_company_types),
            target_seniority=target_seniority,
            # Store the comp floor as NULL when left at 0 (treated as "unset").
            target_min_comp=to_int_or_none(target_min_comp) or None,
            target_remote_ok=1 if target_remote_ok else 0,
            target_locations=text_to_json_list(target_locations),
        )
        st.success("Profile saved.")

st.divider()

# --------------------------------------------------------------------------
# S3 — Skill alias CRUD. The controlled vocabulary behind deterministic skill
# matching (src/skills.py, added in M3).
# --------------------------------------------------------------------------
st.header("Skill Aliases (S3)")
st.caption(
    "Each canonical skill maps to one or more surface forms. Matching is a "
    "deterministic lookup against these aliases — not embeddings (N6)."
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
# S4 — LLM-assisted alias suggestion (stubbed until M4 wires the LLM). The
# suggestion is always a reviewed step: nothing is written without confirmation.
# --------------------------------------------------------------------------
st.header("Suggest aliases (S4)")
st.caption(
    "Propose aliases for your existing canonical skills. Suggestions are a "
    "reviewed step — nothing is written until you confirm. The model never "
    "edits the vocabulary directly."
)

# Canonical skills the user already has — the LLM expands these with surface forms.
canonicals = sorted({row["canonical_skill"] for row in aliases})

if not canonicals:
    st.info("Add at least one skill alias above first, then suggestions can "
            "expand its canonical skill with more surface forms.")
elif st.button("Suggest aliases"):
    try:
        with st.spinner("Asking the LLM for alias suggestions…"):
            result = extraction_mod.suggest_aliases(canonicals, config)
        st.session_state["alias_suggestions"] = [
            s.model_dump() for s in result.suggestions
        ]
    except Exception as exc:  # noqa: BLE001 — surface API/setup errors to the user
        st.error(f"Suggestion failed: {exc}. Is ANTHROPIC_API_KEY set?")

# Render pending suggestions as a reviewed step (S4: write only on confirm).
pending = st.session_state.get("alias_suggestions")
if pending:
    existing = {(r["canonical_skill"], r["alias"]) for r in aliases}
    fresh = [s for s in pending if (s["canonical_skill"], s["alias"]) not in existing]
    if not fresh:
        st.success("All suggested aliases are already in your vocabulary.")
    else:
        st.write("**Review suggestions** — check the ones to add:")
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
