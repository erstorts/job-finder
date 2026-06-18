# Design Decisions (DECISIONS.md)

This file records the four load-bearing design choices behind JAMS and *why*
they are the way they are (PRD DOC6). They are easy to undo accidentally during
a future change, and undoing them quietly breaks the system's core guarantees.
Read this before altering the data model or the scoring/dedup paths.

## 1. Job vs. Listing — entity resolution

**Decision.** A **job** is the real underlying role; a **listing** is one place
it was seen (LinkedIn, a careers page, a board). One job has many listings.

**Why.** Deduplication is a record-linkage problem. Modeling sightings
separately from roles makes "never apply twice" (G1) clean and gives
source-quality analytics for free ("which sites surface jobs worth my time").
Dedup uses *blocking then matching*: bucket by normalized company name, then
compare titles only within the bucket.

**Don't.** Don't collapse listing into job. You would lose the dedup ledger and
per-source analytics, and re-applies become hard to prevent.

## 2. Status as an event log, not a column — event sourcing

**Decision.** Status changes are append-only rows in `status_event`
`(job_id, status, occurred_at, note)`. Current status = the latest event.
History is never updated in place.

**Why.** Like a bank ledger: the balance is derived, the transactions are truth.
This buys time-in-stage analytics, stage-to-stage conversion rates, and a full
audit trail for free, and it naturally handles backwards moves (e.g. rejected
after an onsite) that a single linear status column cannot.

**Don't.** Don't add a mutable `status` column to `job` and update it. That
destroys the funnel analytics and the audit trail.

## 3. LLM as a parser, not a judge

**Decision.** The LLM is used **only** to extract structured facts from pasted
listing text. It never produces the match score or makes dedup decisions. All
judgment is plain Python over the extracted facts.

**Why.** This is the heart of G2 (repeatable, defensible recommendations).
Language models have no reliable numeric calibration: a model-produced "78%
match" is largely noise, drifts between identical calls, and looks deceptively
precise. Deterministic Python over frozen facts is repeatable by construction
and every point traces to a reason.

**Don't.** Don't ask the model to score, rank, or decide. Don't introduce
embedding-based skill matching (N6) — use the deterministic `skill_alias`
lookup so matches stay explainable.

## 4. Frozen extraction — repeatability

**Decision.** The LLM is called exactly once per job, at capture. The validated
extraction is written to `job.extracted` as JSON and **never regenerated**. All
scoring reads only that frozen JSON.

**Why.** LLM calls are not reliably reproducible even at temperature 0 (token
sampling, server-side routing, silent model updates all introduce drift). No
repeatable behavior may depend on a live call. Freezing also stops Streamlit
reruns from re-hitting the API (TECH4/REPEAT2). The rubric version that produced
a score is stored on the job (`rubric_version`) so old scores stay interpretable
after the rubric is tuned.

**Don't.** Don't re-extract an existing job, and don't move scoring inputs out
of the frozen JSON. If extraction quality must improve, version it explicitly
rather than silently overwriting history.
