"""Fixtures for synthetic project tests: build a throwaway project and read the `wave.py run --dry-run` sequence.

Synthetic tests use made-up data only (no real project files). Integration tests reuse `dry_run` on a copy of a real project's config."""

import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline import cli_wave
from tests.support.tmpdirs import tracked_mkdtemp

TABLE = "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\n"
STEP = re.compile(r"^step (\d+) (\S+) (\S+?)/(\S+)\s*(.*)$")


def make_project(flags="", config=None, extra_lines="", audit_md=None, stage="s"):
    """-> project dir with .work/<stage>/TASKS.md (one Haiku task), optional pipeline.json and AUDIT.md."""
    d = tracked_mkdtemp(prefix="synthetic-project-")
    os.makedirs(os.path.join(d, ".work", stage))
    with open(os.path.join(d, ".work", stage, "TASKS.md"), "w", encoding="utf-8") as f:
        f.write(TABLE + (f"flags: {flags}\n" if flags else "") + extra_lines)
    if config is not None:
        with open(os.path.join(d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(config, f)
    if audit_md is not None:
        with open(os.path.join(d, "AUDIT.md"), "w", encoding="utf-8") as f:
            f.write(audit_md)
    return d


def dry_run(d, stage="s"):
    """-> (exit code, steps, last line). steps = [(id, role, model, rest)] in the order the walker dispatched them."""
    out = io.StringIO()
    with redirect_stdout(out), mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}):  # no first code search: about 1 s per step
        rc = cli_wave.main(["run", "--stage", stage, "--dir", d, "--dry-run"])
    lines = out.getvalue().strip().splitlines()
    steps = [(m.group(2), m.group(3), m.group(4), m.group(5)) for m in (STEP.match(x) for x in lines) if m]
    return rc, steps, lines[-1] if lines else ""


def roles(steps):
    return [s[1] for s in steps]
