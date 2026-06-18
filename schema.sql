-- =========================================================================
-- JAMS canonical schema (PRD §6).
--
-- This file is the single source of truth for the database structure. It is
-- executed by src/db.py on first run (CREATE TABLE IF NOT EXISTS), so it is
-- safe to run repeatedly. Per-table and per-column comments are required by
-- DOC4 and must be preserved when the schema changes.
--
-- All *_norm columns hold normalized forms used for matching: lowercased,
-- punctuation and legal suffixes ("Inc."/"LLC") stripped, common
-- abbreviations ("Sr." -> "Senior") expanded. Normalization lives in
-- src/dedup.py; the columns store the precomputed result so blocking is a
-- cheap equality test.
-- =========================================================================

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
