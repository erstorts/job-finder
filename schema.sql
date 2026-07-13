-- =========================================================================
-- JAMS canonical schema.
--
-- This file is the single source of truth for the database structure. It is
-- executed by src/db.py on first run (CREATE TABLE IF NOT EXISTS), so it is
-- safe to run repeatedly.
--
-- The app is an ATS-style evaluator + to-do tracker:
--   Triage   -> score how well the resume/LinkedIn match a listing, flag
--               Denver/remote + cover-letter option + posting age, then
--               pursue (onto the to-do list) or pass.
--   Log      -> the to-do list of jobs to apply to; applying removes the job.
--   Pipeline -> everything applied to, with an outcome (interview/rejected/ghosted).
--   Prep     -> jobs that landed an interview.
--   Analytics-> which inputs correlate with interview outcomes.
--
-- All *_norm columns hold normalized forms used for dedup matching: lowercased,
-- punctuation and legal suffixes ("Inc."/"LLC") stripped, common abbreviations
-- ("Sr." -> "Senior") expanded. Normalization lives in src/dedup.py; the columns
-- store the precomputed result so blocking is a cheap equality test.
-- =========================================================================

-- =========================================================================
-- profile: the single user's resume + LinkedIn text, used to compute the ATS
-- match. Expected to hold exactly one row. Slimmed to just the inputs the
-- keyword match reads (no targeting fields — triage no longer gates on them).
-- =========================================================================
CREATE TABLE IF NOT EXISTS profile (
    id             INTEGER PRIMARY KEY,
    resume_text    TEXT,
    linkedin_text  TEXT
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
-- job: the canonical role. Carries the frozen extracted facts, the ATS score,
-- the triage flags, and (via status_event) its history. One row per real job.
-- =========================================================================
CREATE TABLE IF NOT EXISTS job (
    id                   INTEGER PRIMARY KEY,
    company_name         TEXT,
    company_name_norm    TEXT,           -- blocking key for dedup
    title                TEXT,
    title_norm           TEXT,
    location_type        TEXT,           -- "denver" | "remote" (Denver-local vs remote)
    jd_text              TEXT,           -- the pasted listing text (input of record)
    salary_min           INTEGER,        -- nullable; often absent
    salary_max           INTEGER,        -- nullable
    cover_letter_option  INTEGER,        -- 0/1: does the application offer/ask for a cover letter
    date_posted          TEXT,           -- ISO date the listing was posted (drives "time since posted")
    date_first_seen      TEXT,           -- ISO 8601 when captured
    extracted            TEXT,           -- JSON of the extraction contract. FROZEN after first capture.
    match_score          REAL,           -- 0-100 ATS keyword-coverage score, computed in Python
    match_breakdown      TEXT,           -- JSON: matched/missed keywords + coverage (what to add to resume)
    rubric_version       TEXT,           -- which weight set produced match_score, e.g. "ats-v1"
    decision             TEXT,           -- "pursue" | "pass" | NULL (undecided)
    decided_at           TEXT
);

-- =========================================================================
-- listing: a sighting of a job. The dedup ledger AND the "apply link" store.
-- Many listings -> one job. listing.url is the link the user clicks to apply.
-- =========================================================================
CREATE TABLE IF NOT EXISTS listing (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES job(id),
    source_site  TEXT,                   -- e.g. "linkedin", "greenhouse", "company-site"
    url          TEXT,                   -- the apply link (reference only; never the extraction input)
    raw_title    TEXT,
    date_seen    TEXT
);

-- =========================================================================
-- status: ordered lookup of stages. sort_order gives sequence; is_terminal
-- marks stages a job does not progress out of.
--   found            -> pursued at triage; on the to-do list
--   passed           -> passed at triage; off the to-do list (terminal)
--   applied          -> applied; in the pipeline
--   landed_interview -> outcome: landed an interview
--   rejected         -> outcome: rejection email (terminal)
--   ghosted          -> outcome: no response (terminal)
-- =========================================================================
CREATE TABLE IF NOT EXISTS status (
    name        TEXT PRIMARY KEY,
    sort_order  INTEGER NOT NULL,
    is_terminal INTEGER NOT NULL         -- 0/1
);

-- =========================================================================
-- status_event: the append-only history. Current status of a job = the event
-- with the latest occurred_at. NEVER update in place; always insert.
-- =========================================================================
CREATE TABLE IF NOT EXISTS status_event (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES job(id),
    status       TEXT NOT NULL REFERENCES status(name),
    occurred_at  TEXT NOT NULL,          -- ISO 8601
    note         TEXT
);

-- =========================================================================
-- application: the apply-time labels. 1:1 with job. Created when the user
-- clicks "Applied" on the to-do list. linkedin_contact is the one question the
-- apply popup asks, and doubles as an independent variable in analytics.
-- =========================================================================
CREATE TABLE IF NOT EXISTS application (
    job_id           INTEGER PRIMARY KEY REFERENCES job(id),
    applied_at       TEXT,
    linkedin_contact INTEGER             -- 0/1: found someone on LinkedIn to message
);
