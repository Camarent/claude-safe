"""Slow-suite incident: an acceptance command that hit its time limit is BLOCKED `timed out, not verified`, never FAIL and never a green or red state of the stage.
`record` keeps one row per such command in .work/<stage>/retro/slow-suite.jsonl, so the retro sees which limit was hit; the lead raises tool_settings.script_timeout_s if the limit is too low."""

import os

from .configuration import RETRO_DIR, SLOW_SUITE_FILE, SLOW_SUITE_KIND
from .util import append_jsonl

REASON = "timed out, not verified"  # verdict head of an acceptance step that hit its time limit
QRUN_TIMEOUT_MARK = "qrun: timeout after"  # qrun.py: the per-command limit qrun enforces itself
CANNOT_RUN_MARK = "cannot run:"  # acceptance.qrun_runner: the subprocess timed out (or could not start)
TIMED_OUT_MARK = "timed out"  # the text of a subprocess.TimeoutExpired inside CANNOT_RUN_MARK


def is_timeout(rc, out) -> bool:
    """-> True when a command failed by its time limit: qrun's own timeout line in OUT, or a runner `cannot run: ... timed out` result."""
    if not rc:
        return False
    return QRUN_TIMEOUT_MARK in out or (CANNOT_RUN_MARK in out and TIMED_OUT_MARK in out)


def record(stage_dir, project, step, seconds, limit) -> None:
    """Append one row {kind, project, step, seconds, limit} to STAGE_DIR/retro/slow-suite.jsonl (folder created). SECONDS is the measured run time, LIMIT the limit it hit."""
    row = {"kind": SLOW_SUITE_KIND, "project": project, "step": step, "seconds": round(seconds, 1), "limit": limit}
    append_jsonl(os.path.join(stage_dir, RETRO_DIR, SLOW_SUITE_FILE), row)
