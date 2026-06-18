"""Data-access layer for JAMS — the ONLY module that touches SQLite.

Per PRD TECH2 all SQL is raw and parameterized and lives here; the rest of the
app calls typed functions and never writes inline SQL. Per TECH1 persistence is
SQLite via the standard-library ``sqlite3`` module (no ORM, TECH8/N8).

This module owns three things in M1 (PRD §13 M1 — Foundation):

* connection management with sane pragmas (foreign keys on, Row factory),
* schema creation from the canonical ``schema.sql`` (idempotent), and
* seeding the ``status`` lookup table (DATA1).

Later milestones add typed read/write helpers (jobs, listings, status_events,
applications, analytics queries) to this same module.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from src import dedup


def now_iso() -> str:
    """Current local timestamp as an ISO 8601 string (DATA2).

    Centralized so every write stamps time the same way. This is the one place a
    clock is read; the pure logic modules (dedup, scoring) never touch it
    (REPEAT1).
    """
    return datetime.now().isoformat(timespec="seconds")

# Project root is one level above this src/ package. The schema lives at the
# root; the SQLite file lives under data/ and is git-ignored (TECH8).
_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = _ROOT / "schema.sql"
DEFAULT_DB_PATH = _ROOT / "data" / "jobs.db"

# Seed rows for the pipeline-stage lookup (DATA1). sort_order gives the funnel
# direction; is_terminal marks stages a job does not progress out of. Backwards
# moves are still allowed because current status is derived from the latest
# status_event, not constrained by sort_order (PRD §6 status_event rationale).
STATUS_SEED: tuple[tuple[str, int, int], ...] = (
    # name,             sort_order, is_terminal
    ("found",            0, 0),
    ("applied",          1, 0),
    ("recruiter_screen", 2, 0),
    ("hiring_manager",   3, 0),
    ("onsite",           4, 0),
    ("offer",            5, 1),
    ("rejected",         6, 1),
    ("ghosted",          7, 1),
    ("withdrawn",        8, 1),
)


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with the pragmas JAMS relies on.

    Parameters
    ----------
    db_path:
        Path to the SQLite file. Defaults to ``data/jobs.db`` under the project
        root. Pass ``":memory:"`` in tests for an ephemeral database.

    Returns
    -------
    sqlite3.Connection
        A connection whose ``row_factory`` is :class:`sqlite3.Row` (so callers
        get dict-like rows) and with ``PRAGMA foreign_keys = ON`` enabled (it
        is off by default in SQLite and the schema relies on the FKs).

    Side effects
    ------------
    Creates the parent ``data/`` directory if it does not yet exist.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    # ":memory:" and other special URIs have no parent dir to create.
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Foreign keys are off by default in SQLite; the schema's REFERENCES are
    # only enforced with this pragma set per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables (if absent) and seed the status lookup.

    Idempotent: ``schema.sql`` uses ``CREATE TABLE IF NOT EXISTS`` and the
    status seed uses ``INSERT OR IGNORE``, so calling this on every app start
    is safe (PRD §6: create tables on first run if they do not exist).

    Parameters
    ----------
    conn:
        An open connection from :func:`get_connection`.

    Side effects
    ------------
    Executes DDL and commits seed rows to the database.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    seed_status(conn)
    conn.commit()


def seed_status(conn: sqlite3.Connection) -> None:
    """Insert the canonical pipeline stages, leaving any existing rows intact.

    Uses ``INSERT OR IGNORE`` keyed on the ``status.name`` primary key so
    re-running never duplicates or overwrites stages the owner may have tuned.

    Parameters
    ----------
    conn:
        An open connection. The caller is responsible for committing (``init_db``
        commits once after seeding).
    """
    conn.executemany(
        "INSERT OR IGNORE INTO status (name, sort_order, is_terminal) "
        "VALUES (?, ?, ?)",
        STATUS_SEED,
    )


def initialize(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Convenience: open a connection and ensure the schema exists.

    Returns the ready-to-use connection. This is the single entry point the
    Streamlit app calls on startup so a fresh checkout creates its database on
    first run (PRD §13 M1 acceptance: "App runs and creates the database").
    """
    conn = get_connection(db_path)
    init_db(conn)
    return conn


# =========================================================================
# Profile (PRD S1, S2). The profile table holds exactly one row in v1; we key
# it on the fixed id = 1 so "save" is an upsert rather than a row proliferation.
# =========================================================================

PROFILE_ID = 1


def get_profile(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the single profile row as a dict, or ``None`` if not yet saved.

    Parameters
    ----------
    conn:
        An open connection.

    Returns
    -------
    dict or None
        The profile columns keyed by name, or ``None`` before first save.
    """
    row = conn.execute(
        "SELECT id, resume_text, linkedin_text, target_description, "
        "target_company_types, target_seniority, target_min_comp, "
        "target_remote_ok, target_locations "
        "FROM profile WHERE id = ?",
        (PROFILE_ID,),
    ).fetchone()
    return dict(row) if row is not None else None


def save_profile(
    conn: sqlite3.Connection,
    *,
    resume_text: str | None,
    linkedin_text: str | None,
    target_description: str | None,
    target_company_types: str | None,
    target_seniority: str | None,
    target_min_comp: int | None,
    target_remote_ok: int | None,
    target_locations: str | None,
) -> None:
    """Insert or replace the single profile row (PRD S1, S2).

    Uses ``INSERT ... ON CONFLICT(id) DO UPDATE`` keyed on the fixed
    :data:`PROFILE_ID` so there is always exactly one profile row. JSON columns
    (``target_company_types``, ``target_locations``) are passed pre-serialized
    by the caller and must hold valid JSON or ``NULL`` (DATA2).

    Side effects
    ------------
    Writes and commits the profile row.
    """
    conn.execute(
        "INSERT INTO profile ("
        "  id, resume_text, linkedin_text, target_description, "
        "  target_company_types, target_seniority, target_min_comp, "
        "  target_remote_ok, target_locations"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  resume_text = excluded.resume_text, "
        "  linkedin_text = excluded.linkedin_text, "
        "  target_description = excluded.target_description, "
        "  target_company_types = excluded.target_company_types, "
        "  target_seniority = excluded.target_seniority, "
        "  target_min_comp = excluded.target_min_comp, "
        "  target_remote_ok = excluded.target_remote_ok, "
        "  target_locations = excluded.target_locations",
        (
            PROFILE_ID,
            resume_text,
            linkedin_text,
            target_description,
            target_company_types,
            target_seniority,
            target_min_comp,
            target_remote_ok,
            target_locations,
        ),
    )
    conn.commit()


# =========================================================================
# Skill aliases (PRD S3, S4). Controlled vocabulary for deterministic skill
# matching. The UNIQUE(canonical_skill, alias) constraint prevents duplicates.
# =========================================================================


def list_skill_aliases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all skill_alias rows ordered by canonical skill then alias."""
    rows = conn.execute(
        "SELECT id, canonical_skill, alias FROM skill_alias "
        "ORDER BY canonical_skill, alias"
    ).fetchall()
    return [dict(r) for r in rows]


def add_skill_alias(
    conn: sqlite3.Connection, canonical_skill: str, alias: str
) -> bool:
    """Add one (canonical_skill, alias) pair.

    Uses ``INSERT OR IGNORE`` against the UNIQUE constraint so re-adding an
    existing pair is a no-op rather than an error.

    Returns
    -------
    bool
        ``True`` if a new row was inserted, ``False`` if the pair already existed.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO skill_alias (canonical_skill, alias) "
        "VALUES (?, ?)",
        (canonical_skill, alias),
    )
    conn.commit()
    return cur.rowcount > 0


def update_skill_alias(
    conn: sqlite3.Connection, alias_id: int, canonical_skill: str, alias: str
) -> None:
    """Update the canonical/alias text of an existing skill_alias row."""
    conn.execute(
        "UPDATE skill_alias SET canonical_skill = ?, alias = ? WHERE id = ?",
        (canonical_skill, alias, alias_id),
    )
    conn.commit()


def delete_skill_alias(conn: sqlite3.Connection, alias_id: int) -> None:
    """Delete a skill_alias row by id."""
    conn.execute("DELETE FROM skill_alias WHERE id = ?", (alias_id,))
    conn.commit()


# =========================================================================
# Jobs, listings, status events (PRD T3–T6, §5/§6). A job is the canonical
# role; listings are sightings; status_event is the append-only history.
# =========================================================================


def get_dedup_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the minimal job fields dedup needs (PRD §9.1).

    Only id + normalized keys + jd_text, so blocking/matching never loads full
    rows it does not use.
    """
    rows = conn.execute(
        "SELECT id, company_name_norm, title_norm, jd_text FROM job"
    ).fetchall()
    return [dict(r) for r in rows]


def insert_job(
    conn: sqlite3.Connection,
    *,
    company_name: str | None,
    title: str | None,
    location: str | None,
    remote_flag: int | None,
    jd_text: str | None,
    salary_min: int | None,
    salary_max: int | None,
    benefits: str | None,
    company_description: str | None,
    extracted_json: str | None,
    date_first_seen: str | None = None,
) -> int:
    """Insert a new canonical job and return its id.

    The ``*_norm`` blocking keys are computed here (once, at capture) via the
    pure normalizers in :mod:`src.dedup`, so dedup later compares precomputed
    values. ``extracted_json`` is the frozen extraction contract (X2): written
    once and never regenerated.

    Score and decision are filled in separately by :func:`set_score` and
    :func:`set_decision`.
    """
    date_first_seen = date_first_seen or now_iso()
    cur = conn.execute(
        "INSERT INTO job ("
        "  company_name, company_name_norm, title, title_norm, location, "
        "  remote_flag, jd_text, salary_min, salary_max, benefits, "
        "  company_description, date_first_seen, extracted"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            company_name,
            dedup.normalize_company(company_name),
            title,
            dedup.normalize_title(title),
            location,
            remote_flag,
            jd_text,
            salary_min,
            salary_max,
            benefits,
            company_description,
            date_first_seen,
            extracted_json,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_score(
    conn: sqlite3.Connection,
    job_id: int,
    match_score: float,
    match_breakdown_json: str,
    rubric_version: str,
) -> None:
    """Persist the computed score, breakdown, and rubric version (T4)."""
    conn.execute(
        "UPDATE job SET match_score = ?, match_breakdown = ?, rubric_version = ? "
        "WHERE id = ?",
        (match_score, match_breakdown_json, rubric_version, job_id),
    )
    conn.commit()


def set_decision(
    conn: sqlite3.Connection, job_id: int, decision: str, decided_at: str | None = None
) -> None:
    """Record the user's apply/pass decision and timestamp (T6)."""
    conn.execute(
        "UPDATE job SET decision = ?, decided_at = ? WHERE id = ?",
        (decision, decided_at or now_iso(), job_id),
    )
    conn.commit()


def add_listing(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    source_site: str | None,
    url: str | None,
    raw_title: str | None,
    date_seen: str | None = None,
) -> int:
    """Attach a sighting to a job and return the listing id (T3)."""
    cur = conn.execute(
        "INSERT INTO listing (job_id, source_site, url, raw_title, date_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (job_id, source_site, url, raw_title, date_seen or now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_status_event(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    note: str | None = None,
    occurred_at: str | None = None,
) -> None:
    """Append a status event — NEVER update history in place (PRD §6, P2)."""
    conn.execute(
        "INSERT INTO status_event (job_id, status, occurred_at, note) "
        "VALUES (?, ?, ?, ?)",
        (job_id, status, occurred_at or now_iso(), note),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    """Return a full job row as a dict, or ``None`` if absent."""
    row = conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def get_listings(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    """Return all listings (sightings) for a job, newest first."""
    rows = conn.execute(
        "SELECT id, source_site, url, raw_title, date_seen FROM listing "
        "WHERE job_id = ? ORDER BY date_seen DESC",
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def current_status(conn: sqlite3.Connection, job_id: int) -> str | None:
    """Return a job's current status: the latest status_event by occurred_at.

    Current status is derived, never stored as a column (DECISIONS.md #2). Ties
    on occurred_at are broken by the event id (the later insert wins).
    """
    row = conn.execute(
        "SELECT status FROM status_event WHERE job_id = ? "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return row["status"] if row is not None else None


def list_statuses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all pipeline stages ordered by sort_order (for grids/funnels)."""
    rows = conn.execute(
        "SELECT name, sort_order, is_terminal FROM status ORDER BY sort_order"
    ).fetchall()
    return [dict(r) for r in rows]


def status_history(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    """Return a job's full status_event history, oldest first (audit trail)."""
    rows = conn.execute(
        "SELECT status, occurred_at, note FROM status_event WHERE job_id = ? "
        "ORDER BY occurred_at, id",
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_pipeline(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return one row per job with derived current status and source list (P1).

    Current status and its timestamp are computed from the latest status_event
    via correlated subqueries (status is never a stored column). ``sources`` is a
    comma-separated list of distinct source sites across the job's listings.
    """
    rows = conn.execute(
        """
        SELECT
            j.id              AS job_id,
            j.company_name    AS company_name,
            j.title           AS title,
            j.match_score     AS match_score,
            j.decision        AS decision,
            (SELECT se.status FROM status_event se WHERE se.job_id = j.id
             ORDER BY se.occurred_at DESC, se.id DESC LIMIT 1) AS current_status,
            (SELECT se.occurred_at FROM status_event se WHERE se.job_id = j.id
             ORDER BY se.occurred_at DESC, se.id DESC LIMIT 1) AS last_event_at,
            (SELECT GROUP_CONCAT(DISTINCT l.source_site) FROM listing l
             WHERE l.job_id = j.id) AS sources
        FROM job j
        ORDER BY last_event_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


# =========================================================================
# Application labels (PRD L1, L2). 1:1 with job in v1. Recording an application
# also appends an `applied` status event (the L2 part is done by the caller via
# add_status_event so the timestamp is shared).
# =========================================================================


def save_application(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    applied_at: str | None,
    applied_via: str | None,
    cover_letter: int,
    tailored_resume: int,
    referral: int,
) -> None:
    """Insert or replace the application labels for a job (L1).

    Upsert keyed on the job_id primary key so re-saving updates in place. These
    labels double as the independent variables in the analytics (§A).
    """
    conn.execute(
        "INSERT INTO application ("
        "  job_id, applied_at, applied_via, cover_letter, tailored_resume, referral"
        ") VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(job_id) DO UPDATE SET "
        "  applied_at = excluded.applied_at, "
        "  applied_via = excluded.applied_via, "
        "  cover_letter = excluded.cover_letter, "
        "  tailored_resume = excluded.tailored_resume, "
        "  referral = excluded.referral",
        (job_id, applied_at, applied_via, cover_letter, tailored_resume, referral),
    )
    conn.commit()


def get_application(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    """Return a job's application labels, or ``None`` if not yet logged."""
    row = conn.execute(
        "SELECT job_id, applied_at, applied_via, cover_letter, tailored_resume, "
        "referral FROM application WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def jobs_in_statuses(
    conn: sqlite3.Connection, statuses: Sequence[str]
) -> list[dict[str, Any]]:
    """Return jobs whose current (latest) status is one of ``statuses``.

    Used by the dashboard (active interviews) and interview-prep page. Current
    status is derived from the latest status_event (DECISIONS.md #2).
    """
    pipeline_rows = list_pipeline(conn)
    wanted = set(statuses)
    return [r for r in pipeline_rows if r["current_status"] in wanted]


def analytics_dataset(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Assemble one record per job for the analytics funnel (PRD §A).

    Each record carries the frozen score, the distinct source sites, the
    application labels, and the full ordered status-event history. All funnel and
    segmentation math runs in :mod:`src.analytics` over these records, keeping
    the SQL here and the (pure, testable) computation there.
    """
    records: list[dict[str, Any]] = []
    job_rows = conn.execute(
        "SELECT id, match_score FROM job ORDER BY id"
    ).fetchall()
    for job in job_rows:
        job_id = job["id"]
        events = conn.execute(
            "SELECT status, occurred_at FROM status_event WHERE job_id = ? "
            "ORDER BY occurred_at, id",
            (job_id,),
        ).fetchall()
        sources = conn.execute(
            "SELECT DISTINCT source_site FROM listing WHERE job_id = ? "
            "AND source_site IS NOT NULL",
            (job_id,),
        ).fetchall()
        app = get_application(conn, job_id)
        records.append({
            "job_id": job_id,
            "match_score": job["match_score"],
            "sources": [s["source_site"] for s in sources],
            "labels": {
                "cover_letter": bool(app and app["cover_letter"]),
                "tailored_resume": bool(app and app["tailored_resume"]),
                "referral": bool(app and app["referral"]),
            },
            "events": [dict(e) for e in events],
        })
    return records
