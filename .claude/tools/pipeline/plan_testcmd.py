"""plan_check rule: a `Test:` line that runs bare pytest where the project names its own test command."""

import os
import re

from .configuration import BARE_RUNNER_RE, TEST_BAN_KEY, TEST_BAN_PREFIX, TESTCMD_HINT_KEY, TESTCMD_WARN_PREFIX, gates_of, project
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


def check_bare_test_runner(d, tid, workspace) -> list[str]:
    """`testcmd:` warning (never fails a check) for one task: its `Test:` line runs bare `pytest` / `python -m pytest` while the project names another test command (it needs an environment the job worktree lacks)."""
    m = TEST_LINE_RE.search(read_text(os.path.join(d, tid + ".md")))
    if not m:
        return []
    hint = test_command_hint(project(workspace))
    if not hint:
        return []
    for part in split_commands(m[1].replace("`", " "), amp=False, newline=False):
        if _is_bare(part):
            return [
                f"{TESTCMD_WARN_PREFIX} {tid}: Test runs bare `{part.strip()}` but the project's test command is `{hint}`: use that, or the runner the project notes name"
            ]
    return []


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
