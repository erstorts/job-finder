# JAMS — Job Application Management System

A local, single-user web app for managing a personal job search. It triages
listings into an apply/pass recommendation with a repeatable, explainable match
score; tracks every application through a pipeline with full history; and
surfaces a conversion funnel showing what's working. See
[`job-application-tracker-PRD.md`](job-application-tracker-PRD.md) for the full
specification.

> **Status:** all milestones **M1–M8** (PRD §13) are implemented — foundation,
> setup, deterministic triage core, LLM extraction, application logging,
> pipeline, dashboard, interview prep, and the analytics funnel. The full test
> suite (`pytest`) covers the determinism guarantees (REPEAT4) and the data
> layer.

## Prerequisites

- Python 3.11+
- An Anthropic API key (only needed once LLM extraction lands in M4)

## Install

```bash
pip install -r requirements.txt
```

## Configure secrets

The LLM API key is read from the environment and never committed (PRD TECH8):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Run the app

```bash
streamlit run app.py
```

On first run the app creates `data/jobs.db` (git-ignored) and seeds the
pipeline-stage lookup table.

## Run the tests

```bash
pytest
```

## Configuration

All tunable values — rubric weights, score/dedup thresholds, follow-up day
counts, and the LLM model string — live in [`config.toml`](config.toml). Edit
that file to change behavior; there are no magic numbers in code (PRD TECH7).

## Project layout

```
job-finder/
  app.py                 # Streamlit entry + Dashboard (D1/D2); bootstraps the database
  ui_common.py           # Streamlit-only helpers (connection, JSON coercion)
  pages/
    1_Setup.py           # profile, target, skill aliases, alias suggestion (S1–S4)
    2_Triage.py          # paste/manual capture → dedup → score → decide (T1–T6)
    3_Log.py             # application logging (L1–L2)
    4_Pipeline.py        # editable status grid + staleness flags (P1–P3)
    5_Interview_Prep.py  # frozen details for interviewing jobs (I1)
    6_Analytics.py       # conversion funnel + segmentation (A1–A3)
  src/                   # pure logic, NO Streamlit imports
    db.py                # connection, schema init, all raw parameterized SQL
    config.py            # loads and exposes config.toml
    models.py            # Pydantic schemas (JobExtraction, AliasSuggestions)
    extraction.py        # LLM client + extraction (the only LLM touchpoint)
    dedup.py             # normalization, blocking, matching
    scoring.py           # hard gates + weighted soft criteria
    skills.py            # alias normalization and matching
    pipeline.py          # follow-up / ghosted staleness flags
    analytics.py         # funnel + segmentation
  config.toml            # rubric weights, thresholds, follow-up days, model string
  schema.sql             # annotated canonical DDL (PRD §6)
  tests/                 # pytest determinism + data-layer + integration tests
  data/jobs.db           # SQLite database (git-ignored)
  requirements.txt
  DECISIONS.md           # rationale behind the four load-bearing design choices
```
