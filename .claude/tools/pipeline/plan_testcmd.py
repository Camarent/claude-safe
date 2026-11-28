"""plan_check rule: a `Test:` line that runs bare pytest where the project names its own test command."""

import functools
import os
import re
import subprocess

from .configuration import (
    BARE_RUNNER_RE,
    QRUN_OP_PREFIX,
    QRUN_OP_RE,
    QRUN_OPS,
    TEST_BAN_KEY,
    TEST_BAN_PREFIX,
    TESTCMD_HINT_KEY,
    TESTCMD_WARN_PREFIX,
    TESTRUNNER_PREFIX,
    TESTRUNNER_PROBE,
    TESTRUNNER_PROBE_TIMEOUT_S,
    TESTRUNNER_WORKING_FORM,
    gates_of,
    project,
)
from .plan_commands import _strip_wrappers
from .shellwords import split_commands
from .util import read_text

TEST_LINE_RE = re.compile(r"(?m)^Test:\s*(.*)$")
PYTEST_RE = re.compile(r"\bpytest\b")


def _is_bare(cmd):
    return bool(re.match(BARE_RUNNER_RE, cmd.strip()))


def test_command_hint(cfg) -> str | None:
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


@functools.cache
def pytest_importable() -> bool:
    """-> True when the TESTRUNNER_PROBE exits 0 (pytest imports here). Cached: one check run spawns it once."""
    try:
        done = subprocess.run(TESTRUNNER_PROBE, capture_output=True, check=False, timeout=TESTRUNNER_PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def check_bare_test_runner(d, tid, workspace, probe=pytest_importable) -> list[str]:
    """`testcmd:` error for one task: its `Test:` line runs bare `pytest` / `python -m pytest` while the project names another test command (it needs an environment the job worktree lacks).
    With no test command named, `testrunner:` error when PROBE (a callable -> bool) says pytest does not import with python3."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    bare = next((part for part in split_commands(m[1].replace("`", " "), amp=False, newline=False) if _is_bare(part)), None)
    if bare is None:
        return []
    hint = test_command_hint(project(workspace))
    if hint:
        return [
            f"{TESTCMD_WARN_PREFIX} {tid}: Test runs bare `{bare.strip()}` but the project's test command is `{hint}`: use that, or the runner the project notes name"
        ]
    if probe():
        return []
    return [f"{TESTRUNNER_PREFIX} {tid}: Test runs bare `{bare.strip()}` but pytest does not import with python3 here: use `{TESTRUNNER_WORKING_FORM}`"]


def _test_bans(cfg):
    """-> [(compiled pattern, use text)] from cfg `test_ban`; a malformed entry (not a list, not a dict, no text fields, a bad regex) is ignored."""
    raw = cfg.get(TEST_BAN_KEY)
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("pattern"), str) or not isinstance(item.get("use"), str):
            continue
        try:
            out.append((re.compile(item["pattern"]), item["use"]))
        except re.error:
            continue
    return out


def check_test_ban(d, tid, workspace) -> list[str]:
    """`testban:` error for one task: a `Test:` command part (wrapper words stripped) matches a `test_ban` pattern of the project; the error names the `use` text."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    bans = _test_bans(project(workspace))
    if not bans:
        return []
    for part in split_commands(m[1].replace("`", " "), amp=False, newline=False):
        cmd = " ".join(_strip_wrappers(part.split()))
        for pat, use in bans:
            if pat.search(cmd):
                return [f"{TEST_BAN_PREFIX} {tid}: Test runs `{part.strip()}`, which the project bans (pipeline.json `test_ban`): use {use}"]
    return []


def check_qrun_operator(d, tid) -> list[str]:
    """`qrun-op:` error for one task: a `Test:` line gives a shell operator (`!`, `&&`, `||`, `|`, `;`) as the first word after `cs-qrun --`; cs-qrun runs a program, not a shell, so it exits 127."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    for hit in re.finditer(QRUN_OP_RE, m[1].replace("`", " ")):
        if hit[1] in QRUN_OPS:
            return [
                f"{QRUN_OP_PREFIX} {tid}: Test runs `cs-qrun -- {hit[1]} ...`, a shell operator cs-qrun cannot run (it runs a program, exit 127): wrap the shell part as cs-qrun -- bash -c '...'"
            ]
    return []
