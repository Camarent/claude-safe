"""Named tool settings: the timeouts, the origin remote and the price table the tools use. Each setting has a default here; an env override
`CS_<NAME>` (numbers, and the remote name) and a pipeline.json override under `tool_settings` win over it. A bad value falls back to the default
and leaves a swallowed() line on stderr."""

import math
import os
import re

from ..prices import PRICES
from .errors import ConfigError
from .project import project

TOOL_SETTINGS_KEY = "tool_settings"  # pipeline.json key: {setting key: value}; PROJECT_DEFAULTS holds {}
TOOL_ENV_PREFIX = "CS_"  # env override = this prefix + the setting name, e.g. CS_LSP_RUN_TIMEOUT_S

LSP_RUN_TIMEOUT_S = 20  # seconds one language-server command run may take (search/lsp.py)
ASK_DB_TIMEOUT_S = 60  # sqlite3 connect timeout of the ask-code index (search/ask_code.py)
SAFESHELL_TIMEOUT_S = 60  # default run timeout of localmodel/safeshell.py
INDEX_DB_TIMEOUT_S = 5  # sqlite3 connect timeout of the index step (index_step.py)
SERVE_CONNECT_TIMEOUT_S = 0.5  # socket connect timeout of the servectl port wait (servectl.py)
LSP_LOCK_TIMEOUT_S = 2  # lock wait of the language-server pool shutdown (search/lsp_pool.py)
GIT_REMOTE = "origin"  # the remote a push or fetch names by default
PRICES_TABLE = PRICES  # the price rows of pipeline/prices.py: the default; pipeline.json `tool_settings.prices` replaces it


def _positive(raw):
    """-> raw as a finite number above zero; ValueError otherwise (a bool is not a number here)."""
    if isinstance(raw, bool):
        raise ValueError(f"not a number: {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"not a number: {raw!r}") from e
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"not a positive number: {raw!r}")
    return value


def _remote(raw):
    """-> raw as a remote name: a non-empty string without whitespace; ValueError otherwise."""
    if not isinstance(raw, str) or not re.fullmatch(r"\S+", raw):
        raise ValueError(f"not a remote name: {raw!r}")
    return raw


def _non_negative(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
        raise ValueError(f"not a non-negative number: {raw!r}")
    return raw


def _prices(raw):
    """-> the price rows as tuples (name, input, output, cache_read, cache_write); ValueError unless a non-empty list of such rows."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"not a non-empty list of price rows: {raw!r}")
    rows = []
    for row in raw:
        if not isinstance(row, list) or len(row) != 5 or not isinstance(row[0], str) or not row[0]:
            raise ValueError(f"not a price row [name, input, output, cache_read, cache_write]: {row!r}")
        rows.append((row[0], *(_non_negative(x) for x in row[1:])))
    return tuple(rows)


# name -> (default, pipeline.json key, parser, env override allowed)
_SETTINGS = {
    "LSP_RUN_TIMEOUT_S": (LSP_RUN_TIMEOUT_S, "lsp_run_timeout_s", _positive, True),
    "ASK_DB_TIMEOUT_S": (ASK_DB_TIMEOUT_S, "ask_db_timeout_s", _positive, True),
    "SAFESHELL_TIMEOUT_S": (SAFESHELL_TIMEOUT_S, "safeshell_timeout_s", _positive, True),
    "INDEX_DB_TIMEOUT_S": (INDEX_DB_TIMEOUT_S, "index_db_timeout_s", _positive, True),
    "SERVE_CONNECT_TIMEOUT_S": (SERVE_CONNECT_TIMEOUT_S, "serve_connect_timeout_s", _positive, True),
    "LSP_LOCK_TIMEOUT_S": (LSP_LOCK_TIMEOUT_S, "lsp_lock_timeout_s", _positive, True),
    "GIT_REMOTE": (GIT_REMOTE, "git_remote", _remote, True),
    "PRICES_TABLE": (PRICES_TABLE, "prices", _prices, False),
}


def _swallow(where, exc):
    from ..util import swallowed  # lazy: pipeline.util imports this package

    swallowed(where, exc)


def tool_setting(name):
    """-> the value of the named tool setting (a key of _SETTINGS): env `CS_<NAME>` first (when allowed), then pipeline.json `tool_settings.<key>` of the
    project at the cwd (tools run from the workspace root), then the default. A value that does not parse falls back and is reported."""
    default, key, parse, env_ok = _SETTINGS[name]
    if env_ok:
        raw = os.environ.get(TOOL_ENV_PREFIX + name, "").strip()
        if raw:
            try:
                return parse(raw)
            except ValueError as e:
                _swallow(f"tool_setting {name} (env)", e)
    try:
        block = project(os.getcwd()).get(TOOL_SETTINGS_KEY) or {}
    except ConfigError as e:
        _swallow(f"tool_setting {name} (pipeline.json)", e)
        return default
    if not isinstance(block, dict):
        _swallow(f"tool_setting {name} (pipeline.json)", ValueError(f"`{TOOL_SETTINGS_KEY}` must be an object"))
        return default
    if key in block:
        try:
            return parse(block[key])
        except ValueError as e:
            _swallow(f"tool_setting {name} (pipeline.json)", e)
    return default
