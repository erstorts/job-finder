"""Streamlit-side helpers shared by app.py and the pages/ scripts.

This module is allowed to import Streamlit (unlike everything under src/, per
TECH5). It exists because Streamlit's multipage model runs each page script on
its own — app.py does not run when the user navigates directly to a page — so
every page must independently ensure the database exists before using it.

It also centralizes JSON (de)serialization for the profile's list-valued
columns so pages stay thin.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import streamlit as st

from src.db import get_connection, init_db


@st.cache_resource
def _ensure_initialized() -> bool:
    """Create the schema exactly once per process (idempotent).

    ``st.cache_resource`` memoizes across reruns and pages so init runs a single
    time rather than on every widget interaction (TECH4).
    """
    conn = get_connection()
    init_db(conn)
    conn.close()
    return True


def get_conn() -> sqlite3.Connection:
    """Return a ready-to-use connection, ensuring the schema exists first.

    A fresh connection is opened per call (per Streamlit rerun) rather than
    caching one, because SQLite connections are not safe to share across the
    threads Streamlit may use. Opening is cheap for a local single-user app.
    """
    _ensure_initialized()
    return get_connection()


def json_list_to_text(raw: str | None) -> str:
    """Render a JSON-array column as a comma-separated string for editing.

    Returns an empty string for ``None`` or invalid JSON so the UI degrades
    gracefully rather than raising.
    """
    if not raw:
        return ""
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(items, list):
        return ""
    return ", ".join(str(i) for i in items)


def text_to_json_list(text: str | None) -> str | None:
    """Convert a comma-separated string into a JSON array string, or ``None``.

    Empty input yields ``None`` (stored as SQL NULL) rather than ``"[]"`` so an
    unset field is distinguishable from an explicitly empty one.
    """
    if not text or not text.strip():
        return None
    items = [part.strip() for part in text.split(",") if part.strip()]
    return json.dumps(items) if items else None


def to_int_or_none(value: Any) -> int | None:
    """Coerce a possibly-blank numeric widget value to ``int`` or ``None``."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
