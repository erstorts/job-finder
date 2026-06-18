# Job Application Management System (JAMS) — Product Requirements Document

**Audience:** an AI coding agent (e.g. Claude Code) implementing this application.
**Author:** Emmett Storts (single user / product owner).
**Status:** v1 spec, ready to build.

---

## How to use this document (instructions for the implementing agent)

1. Read the whole PRD before writing code. The "why" notes are load-bearing; they explain design choices the owner will want to preserve.
2. Build in the order given in **§13 Build Sequence**. Complete one milestone, confirm it runs, then move to the next.
3. Treat **§4 Tech Stack & Constraints**, **§10 Documentation & Code Quality**, and **§11 Repeatability** as hard requirements that apply to every milestone, not as optional polish.
4. Each functional requirement has a stable ID (e.g. `T3`). When the owner asks for a change, they will reference these IDs, so keep them intact.
5. Do not build anything listed in **§12 Non-Goals**. If a requirement seems to need something in that list, stop and ask rather than expanding scope.

---

## 1. Overview

JAMS is a local, single-user web app for managing a personal job search. The mental model is a CRM, but the "deals" are job applications moving through a pipeline. It does three jobs:

1. **Triage:** decide whether a given job listing is worth applying to, using a repeatable, explainable match score rather than guesswork.
2. **Track:** record every application and its status over time so nothing falls through the cracks, and so the same job is never applied to twice.
3. **Learn:** surface a conversion funnel that shows which sources and which application tactics correlate with callbacks.

The single most important behavior is answering, the moment the app opens, "what needs my attention right now?" (which applications are stale and need follow-up, which interviews are active). Storing records is secondary to surfacing the next action.

---

## 2. Goals

- **G1.** Never apply to the same underlying job twice, even when it appears on multiple sites with different URLs.
- **G2.** Produce an apply / pass recommendation for each listing that is repeatable (same input gives the same result) and defensible (every point traces to a specific reason).
- **G3.** Track each application through a full pipeline with a complete, time-stamped history.
- **G4.** Surface stale applications that need follow-up without the user having to remember.
- **G5.** Report per-stage conversion and time-in-stage, segmented by source and by application tactic.
- **G6.** Be fully editable by a single non-team owner. Code is the documentation; tunable values live in config, not buried in logic.

## 3. Users & Context

- One user, the owner, running the app locally on their own machine. There is no other audience.
- The user finds listings manually across many sites, copies the listing text into the app, applies on the original site themselves, and returns to log the result.
- Because it is single-user and local, there is no authentication, no multi-tenancy, and no hosting. See **§12 Non-Goals**.

---

## 4. Tech Stack & Constraints

These are hard requirements (`TECH` IDs).

- **TECH1 — Language.** All application logic is written in **Python** (3.11+). All persistence and all queries use **SQL** against **SQLite** via the standard-library `sqlite3` module. No other languages.
- **TECH2 — No ORM.** Use raw, parameterized SQL. The owner is a data engineer and wants the SQL to be visible and editable. Isolate all SQL in a single data-access module (`src/db.py`); the rest of the app calls typed functions, never inline SQL. Rationale: an ORM would hide the SQL the owner explicitly wants to see and change.
- **TECH3 — UI.** Use **Streamlit** for the interface, with its native multipage structure (a `pages/` directory). Use `st.data_editor` for editable grids and Streamlit's built-in charts (`st.bar_chart`, `st.line_chart`) for analytics to keep dependencies minimal.
- **TECH4 — Streamlit execution model.** Remember that Streamlit reruns the entire page script top to bottom on every widget interaction. Therefore: never do read-modify-write logic that assumes a value persists across reruns without `st.session_state`; keep all durable state in SQLite; and never call the LLM inside the normal rerun path (see **REPEAT2**). Rationale: this rerun behavior is the reason SQLite was chosen over CSV files (CSV read-modify-write under reruns risks races and truncated files).
- **TECH5 — Separation of logic from UI.** All deterministic logic (dedup, scoring, skill matching, analytics) lives in plain Python modules under `src/` with **no Streamlit imports**, so it can be unit-tested directly and is unaffected by reruns. Streamlit pages are thin: they gather input, call `src/` functions, and render results.
- **TECH6 — Allowed third-party libraries.** `pydantic` (extraction schema + validation), `rapidfuzz` (fuzzy string matching for dedup), `pandas` (analytics and grid data), `anthropic` (LLM extraction client), `pytest` (tests). Do not add libraries beyond these without asking.
- **TECH7 — Configuration.** All tunable values (rubric weights, score thresholds, dedup thresholds, follow-up day counts, the LLM model string) live in a single `config.toml`, loaded once via `tomllib`. No magic numbers in logic. Rationale: the owner must be able to tune behavior without editing code.
- **TECH8 — Secrets.** The LLM API key is read from the `ANTHROPIC_API_KEY` environment variable. Never hard-code it and never commit it. The SQLite file and any `.env` are git-ignored.

### LLM provider note

The only LLM touchpoint is structured extraction at capture time (see **T2**). Default to **Anthropic Claude** via the official `anthropic` Python SDK, using **structured output (tool use)** to enforce the extraction schema, and validate the result with Pydantic. A sensible default model is `claude-sonnet-4-6` (good structured-extraction quality at low cost); pin it in `config.toml`. Confirm current model strings and the structured-output API against the official docs before implementing: API overview at https://docs.claude.com/en/api/overview and the docs map at https://docs.claude.com/en/docs_site_map.md . The provider call sits behind a thin interface in `src/extraction.py` so it can be swapped later.

---

## 5. Core Concepts (domain model rationale)

Three ideas drive the data model. Understanding them is what lets the owner extend the system safely.

**Job vs. Listing (entity resolution).** A **job** is the real underlying role you might apply to. A **listing** is one place you saw it (LinkedIn, a company careers page, a job board). One job can have many listings. Deduplication is therefore a record-linkage problem: when a new listing arrives, decide whether it points to a job already on file. The standard technique is *blocking then matching*: first bucket candidates by a cheap key (normalized company name), then do the careful comparison only inside that bucket. The analogy is a librarian shelving by section before comparing two editions, rather than scanning the whole library. This split is what makes G1 (no double-applies) clean and also gives source-quality analytics for free.

**Status as an event log, not a column (event sourcing).** Rather than a single mutable `status` field on a job, status changes are recorded as a log of events: `(job_id, status, occurred_at, note)`. The current status is simply the most recent event. The analogy is a bank account: the current balance is not the source of truth, the transaction ledger is, and the balance is the latest derived value. This buys three things for free: time-in-stage analytics, conversion rates between stages, and a full audit trail. It also handles backwards moves (rejected after an interview) that a strict linear status field cannot.

**LLM as a parser, not a judge.** The LLM is used only to extract structured facts from pasted listing text. It never produces the match score. All judgment (dedup decisions, the match score) is plain Python over the extracted facts. Rationale, which is the heart of G2: language models have no reliable numeric calibration, so a model-produced "78% match" is largely noise and drifts between identical calls, while looking deceptively precise. Deterministic Python over frozen facts is repeatable by construction and every point is traceable to a reason. This is a well-documented limitation of LLM scoring, and the fix is to keep scoring out of the model entirely.

---

## 6. Data Model (canonical schema)

This is the canonical schema. Implement it verbatim in `schema.sql` with these comments preserved, and create the tables on first run if they do not exist. All `*_norm` columns are normalized forms used for matching (lowercased, punctuation and legal suffixes such as "Inc."/"LLC" stripped, abbreviations like "Sr." expanded to "Senior").

```sql
-- =========================================================================
-- profile: the single user's resume, LinkedIn, and what they are targeting.
-- Expected to hold exactly one row in v1.
-- =========================================================================
CREATE TABLE IF NOT EXISTS profile (
    id                    INTEGER PRIMARY KEY,
    resume_text           TEXT,
    linkedin_text         TEXT,
    target_description    TEXT,          -- free prose, e.g. "early-stage SaaS startups"
    target_company_types  TEXT,          -- JSON array, e.g. ["saas","startup"]; enables computable target-fit
    target_seniority      TEXT,          -- ordinal band: intern|junior|mid|senior|staff|lead|manager
    target_min_comp       INTEGER,       -- annual floor, nullable
    target_remote_ok      INTEGER,       -- 0/1
    target_locations      TEXT           -- JSON array of acceptable locations
);

-- =========================================================================
-- skill_alias: controlled vocabulary that makes skill matching repeatable.
-- Each canonical skill maps to one or more surface forms. Matching is a
-- deterministic lookup against normalized aliases, NOT embedding similarity.
-- =========================================================================
CREATE TABLE IF NOT EXISTS skill_alias (
    id               INTEGER PRIMARY KEY,
    canonical_skill  TEXT NOT NULL,      -- e.g. "orchestration"
    alias            TEXT NOT NULL,      -- e.g. "airflow", "prefect", "workflow scheduler"
    UNIQUE(canonical_skill, alias)
);

-- =========================================================================
-- job: the canonical role. Carries the frozen extracted facts, the score,
-- the decision, and (via status_event) its history. One row per real job.
-- =========================================================================
CREATE TABLE IF NOT EXISTS job (
    id                   INTEGER PRIMARY KEY,
    company_name         TEXT,
    company_name_norm    TEXT,           -- blocking key for dedup
    title                TEXT,
    title_norm           TEXT,
    location             TEXT,
    remote_flag          INTEGER,        -- 0/1
    jd_text              TEXT,           -- the pasted listing text (input of record)
    salary_min           INTEGER,        -- nullable; often absent
    salary_max           INTEGER,        -- nullable
    benefits             TEXT,           -- nullable
    company_description  TEXT,           -- nullable
    date_first_seen      TEXT,           -- ISO 8601
    extracted            TEXT,           -- JSON of the full extraction contract (see §8). FROZEN after first capture.
    match_score          REAL,           -- 0-100, computed in Python (see §9)
    match_breakdown      TEXT,           -- JSON: per-criterion sub-scores, matched/missed skills, gate results
    rubric_version       TEXT,           -- which weight set produced match_score, e.g. "v1"
    decision             TEXT,           -- "apply" | "pass" | NULL (undecided)
    decided_at           TEXT
);

-- =========================================================================
-- listing: a sighting of a job. The dedup ledger AND the data behind
-- "which sites surface jobs worth my time". Many listings -> one job.
-- =========================================================================
CREATE TABLE IF NOT EXISTS listing (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES job(id),
    source_site  TEXT,                   -- e.g. "linkedin", "greenhouse", "company-site"
    url          TEXT,                   -- reference only; never the extraction input
    raw_title    TEXT,
    date_seen    TEXT
);

-- =========================================================================
-- status: ordered lookup of pipeline stages. sort_order gives sequence so
-- the funnel knows direction; backwards moves are still allowed (a later
-- event can point to an earlier-ordered status).
-- =========================================================================
CREATE TABLE IF NOT EXISTS status (
    name        TEXT PRIMARY KEY,        -- found|applied|recruiter_screen|hiring_manager|onsite|offer|rejected|ghosted|withdrawn
    sort_order  INTEGER NOT NULL,
    is_terminal INTEGER NOT NULL         -- 0/1; rejected/offer/withdrawn/ghosted are terminal
);

-- =========================================================================
-- status_event: the append-only history. Current status of a job = the
-- event with the latest occurred_at. NEVER update in place; always insert.
-- =========================================================================
CREATE TABLE IF NOT EXISTS status_event (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES job(id),
    status       TEXT NOT NULL REFERENCES status(name),
    occurred_at  TEXT NOT NULL,          -- ISO 8601
    note         TEXT
);

-- =========================================================================
-- application: the Page-2 labels. 1:1 with job in v1. These labels double
-- as the independent variables in the "what's working" analysis (§ A).
-- =========================================================================
CREATE TABLE IF NOT EXISTS application (
    job_id          INTEGER PRIMARY KEY REFERENCES job(id),
    applied_at      TEXT,
    applied_via     TEXT,                -- e.g. "company-site", "linkedin-easy-apply", "referral"
    cover_letter    INTEGER,             -- 0/1
    tailored_resume INTEGER,             -- 0/1
    referral        INTEGER              -- 0/1
);
```

**DATA1.** Seed the `status` table on first run with the stages above and sensible `sort_order` / `is_terminal` values.
**DATA2.** Store all timestamps as ISO 8601 strings. JSON columns hold valid JSON or `NULL`.

---

## 7. Functional Requirements

Each requirement has an ID and acceptance criteria (AC). Pages map to **§13** but the requirements are the source of truth.

### Setup (profile, target, skill aliases)

- **S1.** Let the user paste or upload resume text and LinkedIn text, and persist them to the single `profile` row.
- **S2.** Let the user define the target: free-text description plus structured fields (company types, seniority band, comp floor, remote OK, locations).
- **S3.** Let the user view, add, edit, and delete `skill_alias` rows.
- **S4.** Offer an LLM-assisted "suggest aliases" action that proposes aliases for the user's canonical skills as a reviewed step. Suggestions are only written to `skill_alias` after the user confirms. The LLM never edits the vocabulary at runtime.
- **AC:** reloading the app shows the saved profile, target, and alias table. Editing and saving updates the database.

### Triage — the Apply / Pass page

- **T1.** Primary input is **pasted listing text** in a text area. A URL field is optional and is stored on the `listing` only as a reference. Never fetch or scrape the URL as the extraction input. Rationale: major job sites render with JavaScript or block scrapers, so link-fetching is unreliable; pasted text is robust.
- **T2.** On submit, call the LLM to extract the listing into the schema in **§8**, validate with Pydantic, and require `null` for any field not present in the text (no guessing).
- **T3.** Run deduplication (algorithm in **§9.1**) against existing jobs. Resolve to one of:
  - **New job:** create `job` + first `listing` + a `status_event` of `found`.
  - **Confident duplicate:** attach a new `listing` to the existing job and display "already logged from {source} on {date}, current status: {status}." This is the G1 guard against re-applying.
  - **Borderline:** show the candidate match and let the user confirm the merge or mark it as a distinct job.
- **T4.** Compute the match score (algorithm in **§9.2**) in Python over the extracted facts, the profile, and the active rubric in config. Persist `match_score`, `match_breakdown`, and `rubric_version` on the job.
- **T5.** Display the apply / pass recommendation versus the configurable threshold, and always show the full breakdown beneath it: hard-gate results, matched vs. missed required and preferred skills, and each weighted sub-score. The number is never shown alone.
- **T6.** Record the user's decision (`apply` / `pass`) and `decided_at`.
- **AC:** pasting a listing already on file (different URL, same company and title) yields the confident-duplicate path and shows the prior status. The same pasted text scored twice yields identical `match_score` and `match_breakdown`.

### Application logging — the Apply page

- **L1.** After the user applies externally, let them record application labels into `application`: `applied_at`, `applied_via`, `cover_letter`, `tailored_resume`, `referral`.
- **L2.** Recording an application appends a `status_event` of `applied`.
- **AC:** the logged labels persist and the job's current status becomes `applied`.

### Status & Pipeline

- **P1.** Show an editable grid (`st.data_editor`) of jobs with company, title, current status (the latest `status_event`), days since last event, and source(s).
- **P2.** Changing a status in the grid **appends** a new `status_event`; it never overwrites history. Support all stages including backwards moves (e.g. `onsite` then `rejected`) and terminal states.
- **P3.** Derive a "needs follow-up" flag and a "likely ghosted" flag from time since the last event, using thresholds from config (default: follow-up at 7 days in a non-terminal, ghosted at 21 days with no response). These are computed, not manually set.
- **AC:** updating status in the grid adds a row to `status_event` and leaves prior rows intact. A job with no update for longer than the configured window is flagged.

### Dashboard (home / landing page)

- **D1.** The landing page surfaces, in priority order: applications needing follow-up (stale beyond threshold), active interviews (jobs in `recruiter_screen` through `onsite`), recent activity, and headline funnel metrics.
- **D2.** Each surfaced item links to the relevant detail (pipeline row or interview-prep entry).
- **AC:** opening the app immediately shows what needs attention without further clicks.

### Interview prep

- **I1.** List jobs currently in an interview stage. For each, show the frozen `jd_text`, `company_description`, salary, benefits, and the source `listing` link(s) for preparation.
- **AC:** an interview-stage job displays its originally captured details and a working link to the source.

### Analytics — the conversion funnel

- **A1.** Compute and display per-stage conversion rates and average time-in-stage from `status_event`.
- **A2.** Segment the funnel by `source_site`, by match-score band, and by application labels (`cover_letter`, `tailored_resume`, `referral`).
- **A3.** Display a visible caveat with the analytics: this data is observational, low-n, and confounded, so it is directional, not causal. (For example, cover letters tend to be written for better-fit jobs, so a cover-letter "lift" may just be a fit effect.) Rationale: prevents the owner, who has a quantitative background, from over-reading small uncontrolled differences as causal effects.
- **AC:** the funnel renders from event data and updates as statuses change; segmentation by source and by each label is available.

---

## 8. LLM Extraction Contract

Define this as a Pydantic model in `src/models.py`. The LLM must populate it via structured output; fields not present in the listing text must be `null` (or empty list), never invented.

```python
class JobExtraction(BaseModel):
    company_name: str | None
    title: str | None
    location: str | None
    remote_flag: bool | None
    salary_min: int | None
    salary_max: int | None
    benefits: str | None
    company_description: str | None
    company_types: list[str]          # e.g. ["saas", "startup"]; [] if unclear
    required_skills: list[str]        # skills the listing marks as required
    preferred_skills: list[str]       # nice-to-have skills
    min_years: int | None             # captured but NOT scored in v1
    degree_required: bool | None
    seniority: str | None             # ordinal band, same vocabulary as profile
    hard_constraints: list[str]       # e.g. ["security clearance", "on-site NYC", "US citizenship"]
```

**X1.** Validate every extraction against this model before persisting. On validation failure, surface the error and do not write a partial job.
**X2.** Persist the full validated object as JSON in `job.extracted`. This JSON is **frozen**: it is written once at capture and never regenerated. All later scoring reads from it.

---

## 9. Algorithms

Implement both as pure functions in `src/` with unit tests. Both must be deterministic.

### 9.1 Deduplication (`src/dedup.py`)

```
def find_duplicate(new_extraction, existing_jobs, config) -> match_result:
    # 1. NORMALIZE
    company_norm = normalize_company(new_extraction.company_name)   # lowercase, strip Inc./LLC/punctuation
    title_norm   = normalize_title(new_extraction.title)            # lowercase, expand Sr.->Senior, etc.

    # 2. BLOCK: keep only existing jobs whose company_name_norm == company_norm
    candidates = [j for j in existing_jobs if j.company_name_norm == company_norm]

    # 3. MATCH within the block using rapidfuzz token_sort_ratio on title_norm,
    #    with a light location tiebreak.
    best = max over candidates of fuzz.token_sort_ratio(title_norm, j.title_norm)

    # 4. DECIDE against config thresholds (defaults shown):
    #    best >= 90  -> "confident_duplicate" (attach listing, block re-apply)
    #    75 <= best < 90 -> "borderline" (ask the user to confirm/merge)
    #    best < 90 with no company block, or best < 75 -> "new_job"
```

**Edge case (DEDUP-E1).** Staffing agencies and "Confidential" postings break the company blocking key. When `company_name` is generic or missing, fall back to matching on title plus a `jd_text` similarity check, or default to `borderline` so the user decides. Document this fallback in code.

Rationale: deterministic rules make every merge explainable ("merged because same normalized company and title ratio 0.94"), which is the same defensibility property required of scoring.

### 9.2 Match scoring (`src/scoring.py`, `src/skills.py`)

Scoring is a pure function of the frozen extraction, the profile, and the rubric config. Two stages:

**Hard gates first.** If any gate fails, decision defaults to `pass` and the failing gate is recorded in `match_breakdown`:
- `degree_required` is true and the profile indicates no matching degree.
- Extracted `seniority` is more than one band away from `target_seniority`.
- A `hard_constraint` the user cannot meet is present (e.g. on-site in a non-target location while `target_remote_ok` is false, or a clearance the user lacks). Keep the not-met constraint set in config.

**Then weighted soft criteria** produce a 0-100 composite. Default weights (rubric `v1`, all in `config.toml`):

| Sub-score | What it measures | Default weight |
|---|---|---|
| required_skill_coverage | fraction of `required_skills` matched via the alias table | 0.45 |
| preferred_skill_coverage | fraction of `preferred_skills` matched | 0.20 |
| seniority_alignment | closeness of job seniority to target band | 0.15 |
| target_fit | overlap of `company_types` with `target_company_types` | 0.15 |
| comp_floor_met | salary range meets `target_min_comp` (1/0/unknown) | 0.05 |

**Default apply threshold:** 60. Configurable. The `match_breakdown` JSON records each sub-score, the matched and missed skill lists, and all gate results.

**Skill matching (`src/skills.py`).** A required or preferred skill counts as matched if its normalized form maps, through `skill_alias`, to a canonical skill present in the user's resume/profile. This is a deterministic lookup. Do not use embedding similarity. Rationale: a lookup is repeatable and explainable ("matched 7 of 9 required skills, missing Spark and Kubernetes"); a cosine-similarity threshold is neither, and explainability is the priority here.

**Not scored in v1:** `min_years` is captured but not scored, because reliably extracting the user's years per skill from a resume is messy. Note it as a future enhancement.

---

## 10. Documentation & Code Quality Requirements

These are explicit owner requirements (`DOC` IDs). The owner will maintain this code alone, so documentation is a deliverable, not an afterthought.

- **DOC1.** Every module has a module-level docstring stating its purpose and how it fits the whole.
- **DOC2.** Every function has a docstring (NumPy or Google style) describing its arguments, return value, and any side effects, plus full type hints on signatures.
- **DOC3.** Non-obvious logic carries inline comments explaining the reasoning, especially the dedup blocking/matching steps and each scoring rule. Comment the *why*, not just the *what*.
- **DOC4.** `schema.sql` keeps the per-table and per-column comments from **§6**.
- **DOC5.** Provide a `README.md` covering: prerequisites, install (`pip install -r requirements.txt`), setting `ANTHROPIC_API_KEY`, running the app (`streamlit run app.py`), running tests (`pytest`), and the project layout.
- **DOC6.** Provide a short `DECISIONS.md` capturing the rationale behind the four key choices (entity resolution / job-vs-listing, event-log status, LLM-as-parser-not-judge, frozen-extraction repeatability) so a future change does not accidentally undo them.
- **DOC7.** Pin dependencies in `requirements.txt`.

---

## 11. Repeatability & Determinism Requirements

Direct from goal G2 (`REPEAT` IDs).

- **REPEAT1.** Scoring (`src/scoring.py`) and dedup (`src/dedup.py`) are pure deterministic functions of their inputs. The same inputs always produce the same output. No randomness, no clock-dependent branching, no network calls.
- **REPEAT2.** The LLM is called exactly once per job, at capture, for extraction only. The result is frozen to `job.extracted` and never regenerated. Scoring reads only the frozen JSON. Rationale: LLM calls are not reliably reproducible even at temperature 0 (token sampling, server-side routing, silent model updates all introduce drift), so no repeatable behavior may depend on a live call. Freezing also stops Streamlit reruns from re-hitting the API.
- **REPEAT3.** Rubric weights, thresholds, and dedup thresholds live in `config.toml` under a version label. The version that produced a score is stored on the job (`rubric_version`), so historical scores stay interpretable after the owner tunes the rubric.
- **REPEAT4.** Unit tests in `tests/` lock in the determinism: given fixed inputs, `test_scoring.py`, `test_dedup.py`, and `test_skills.py` assert exact expected outputs, including gate behavior and known duplicate/non-duplicate pairs.

---

## 12. Non-Goals (do NOT build)

- **N1.** No authentication, user accounts, or multi-user support. Single local user only.
- **N2.** No hosting, deployment, or cloud infrastructure. Runs locally.
- **N3.** No automated job scraping, crawling, or URL fetching. The user pastes listing text.
- **N4.** No browser extension.
- **N5.** No email or calendar integration in v1.
- **N6.** No machine-learning model training, and no embedding-based matching. Matching is rules plus the alias table.
- **N7.** No LLM call at score time, and no model-produced numeric match score, ever.
- **N8.** No ORM and no database engine other than SQLite.

If a task appears to require any of the above, stop and ask the owner rather than expanding scope.

---

## 13. Build Sequence

Build in this order. The deterministic core comes before the LLM, because the core is the defensible, testable heart of the system and the LLM is an additive layer on top. Get sign-off after each milestone.

- **M1 — Foundation.** Project skeleton (see **§14**), `config.toml`, `src/db.py` with schema creation and seeded `status` table, `requirements.txt`, empty `tests/`. App runs and creates the database.
- **M2 — Setup page.** `S1`–`S4` (alias suggestion can be stubbed until M4 wires the LLM).
- **M3 — Triage core, no LLM.** Manual-entry path for the extraction fields, plus `dedup.py`, `scoring.py`, `skills.py` and their tests (`T3`–`T6`, `§9`, `REPEAT1`/`REPEAT4`). This proves G1 and G2 before any model is involved.
- **M4 — LLM extraction.** `src/extraction.py` and the `JobExtraction` contract (`T1`, `T2`, `X1`, `X2`, `S4`, `REPEAT2`). Triage now accepts pasted text end to end.
- **M5 — Application logging + pipeline.** `L1`–`L2`, `P1`–`P3`.
- **M6 — Dashboard.** `D1`–`D2` as the landing page.
- **M7 — Interview prep.** `I1`.
- **M8 — Analytics.** `A1`–`A3`.

---

## 14. Proposed Project Structure

```
job-tracker/
  app.py                 # Streamlit entry + navigation; renders the dashboard (D1)
  pages/
    1_Setup.py
    2_Triage.py
    3_Log.py
    4_Pipeline.py
    5_Interview_Prep.py
    6_Analytics.py
  src/                   # pure logic, NO Streamlit imports
    db.py                # connection, schema init, all raw parameterized SQL
    models.py            # Pydantic schemas (JobExtraction, etc.)
    extraction.py        # LLM client + extraction (the only LLM touchpoint)
    dedup.py             # normalization, blocking, matching
    scoring.py           # hard gates + weighted soft criteria
    skills.py            # alias normalization and matching
    analytics.py         # funnel + segmentation (pandas)
    config.py            # loads and exposes config.toml
  config.toml            # rubric weights, thresholds, follow-up days, model string
  schema.sql             # annotated DDL (the canonical schema, §6)
  tests/
    test_dedup.py
    test_scoring.py
    test_skills.py
  data/
    jobs.db              # SQLite database (git-ignored)
  requirements.txt
  README.md
  DECISIONS.md
  .gitignore             # ignores data/jobs.db, .env, __pycache__
```

---

## 15. Open Questions / Future Enhancements

- **O1.** LLM provider/model is configurable; Anthropic Claude is the assumed default. Confirm the current model string at build time.
- **O2.** Multiple target profiles (e.g. data scientist vs. data engineer tracks) are out of scope for v1 but the `profile` table could be extended to several rows later.
- **O3.** Scoring `min_years` against extracted user experience is deferred (needs reliable per-skill years from the resume).
- **O4.** Bulk import of already-submitted applications is deferred.
