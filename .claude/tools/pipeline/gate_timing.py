"""Gate run time against the `gate_time_warn_s` limits (pipeline.json, seconds per gate kind: unit, merge, full).
`warn` gives the warning line of one run; `check` reads the gate-run records of a repo (gate_record.records) and warns on each run over its limit.
Warnings only: never a verdict, never a changed exit code."""

from . import gate_record
from .configuration import (
    GATE_KIND_FULL,
    GATE_KIND_MERGE,
    GATE_KIND_TASK,
    GATE_LEVEL_FULL,
    GATE_LEVEL_MERGE,
    GATE_LEVEL_UNIT,
    GATE_TIME_WARN_KEY,
    ConfigError,
    project,
)

LEVEL_UNIT = GATE_LEVEL_UNIT  # gate_time_warn_s key of a task gate run
LEVEL_MERGE = GATE_LEVEL_MERGE  # gate_time_warn_s key of a merge gate run
LEVEL_FULL = GATE_LEVEL_FULL  # gate_time_warn_s key of the final full gate run
LEVEL_OF_KIND = {
    GATE_KIND_TASK: LEVEL_UNIT,
    GATE_KIND_MERGE: LEVEL_MERGE,
    GATE_KIND_FULL: LEVEL_FULL,
}  # gate-run record `kind` (PIPELINE_GATE_KIND at the run) -> level
LINES_MAX = 15  # warning lines check returns at most (the newest)


def warn(level, secs, limits) -> str:
    """-> the warning line of a gate run of kind LEVEL that took SECS seconds, or "" when it is within the limit in LIMITS (gate_time_warn_s)."""
    limit = limits.get(level)
    if secs is None or limit is None or secs <= limit:
        return ""
    return f"gate time: {level} gate run {secs:.0f}s over the {limit}s limit (gate_time_warn_s.{level})"


def check(cwd, level) -> list[str]:
    """-> warning lines for the recorded gate runs of the repo at cwd of the gate kind LEVEL (a record whose kind maps to LEVEL) whose seconds are over its limit, newest last (at most LINES_MAX); [] when nothing can be read."""
    try:
        limits = project(cwd)[GATE_TIME_WARN_KEY]
        lines = [w for rec in gate_record.records(cwd) if LEVEL_OF_KIND.get(rec.get("kind", "")) == level and (w := warn(level, rec.get("secs"), limits))]
    except (OSError, ValueError, KeyError, TypeError, ConfigError):
        return []
    return lines[-LINES_MAX:]
