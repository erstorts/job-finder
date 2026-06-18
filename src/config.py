"""Configuration loader for JAMS.

Loads ``config.toml`` once and exposes it as a plain dict. All tunable values
(rubric weights, thresholds, dedup thresholds, follow-up day counts, the LLM
model string) live in that file per PRD TECH7; no logic module should hold a
magic number. This module has no Streamlit imports so it is safe to call from
both ``src/`` logic and Streamlit pages.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

# config.toml lives at the project root, one level above this src/ package.
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@lru_cache(maxsize=1)
def get_config(path: str | None = None) -> dict[str, Any]:
    """Load and cache the parsed ``config.toml``.

    Parameters
    ----------
    path:
        Optional override path to a config file. Primarily for tests; when
        ``None`` the project-root ``config.toml`` is used.

    Returns
    -------
    dict
        The parsed configuration. Cached after the first call so the file is
        read once per process (PRD TECH7).
    """
    config_file = Path(path) if path is not None else CONFIG_PATH
    with config_file.open("rb") as fh:
        return tomllib.load(fh)
