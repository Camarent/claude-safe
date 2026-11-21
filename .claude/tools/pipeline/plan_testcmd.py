"""plan_check rule: a `Test:` line that runs bare pytest where the project names its own test command."""

import os
import re

from .configuration import BARE_RUNNER_RE, TESTCMD_HINT_KEY, TESTCMD_WARN_PREFIX, gates_of, project
from .shellwords import split_commands
from .util import read_text

TEST_LINE_RE = re.compile(r"(?m)^Test:\s*(.*)$")
PYTEST_RE = re.compile(r"\bpytest\b")


def _is_bare(cmd):
    return bool(re.match(BARE_RUNNER_RE, cmd.strip()))


def test_command_hint(cfg):
    """-> the project's named test command: setting `test_command_hint`, else the first gate command that runs pytest behind a wrapper; None when there is none."""
    hint = cfg.get(TESTCMD_HINT_KEY)
    if isinstance(hint, str) and hint.strip():
        return hint.strip()
    try:
        gates = gates_of(cfg)
    except ValueError:
        return None
    for g in gates:
        for part in split_commands(g["cmd"], amp=False, newline=False):
            if PYTEST_RE.search(part) and not _is_bare(part):
                return part.strip()
    return None


def check_bare_test_runner(d, tid, workspace):
    """`testcmd:` warning (never fails a check) for one task: its `Test:` line runs bare `pytest` / `python -m pytest` while the project names another test command (it needs an environment the job worktree lacks)."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    hint = test_command_hint(project(workspace))
    if not hint:
        return []
    for part in split_commands(m[1].replace("`", " "), amp=False, newline=False):
        if _is_bare(part):
            return [f"{TESTCMD_WARN_PREFIX} {tid}: Test runs bare `{part.strip()}` but the project's test command is `{hint}`: use that, or the runner the project notes name"]
    return []
