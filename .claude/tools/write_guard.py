#!/usr/bin/env python3
"""write_guard: PreToolUse hook for Write, Edit, MultiEdit and NotebookEdit in a dev session.

Active when the env var PIPELINE_GUARD_WORKTREE (pipeline.configuration.ENV_GUARD_WORKTREE) holds the job worktree root.
Without it the hook does nothing and exits 0 (today's behaviour).

With it, a write is denied when its real path (symlinks and `..` resolved) is under the main checkout (ENV_GUARD_MAIN),
but neither under the job worktree nor under a dir listed in ENV_GUARD_ALLOW (os.pathsep list). Every other path is allowed.
Each denial appends one JSON line {ts, job, tool, path} to the file named by ENV_GUARD_LOG (job = ENV_GUARD_JOB);
a log failure never changes the deny.

Reads the hook JSON on stdin; prints a deny decision, or nothing to allow. Exit 0, except 2 for malformed input
while the worktree variable is set (fail closed).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline.configuration import ENV_GUARD_ALLOW, ENV_GUARD_JOB, ENV_GUARD_LOG, ENV_GUARD_MAIN, ENV_GUARD_WORKTREE, GUARD_TOOLS
from review_guard import under

PATH_KEYS = ("file_path", "notebook_path")  # the tool_input key that names the written file


def real(path, cwd):
    """Real absolute path of `path`; a relative path is taken from the session `cwd`."""
    return os.path.realpath(os.path.join(cwd, path))


def target_path(data):
    """The written file of a guarded tool call, or '' when the call names none."""
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return ""
    for key in PATH_KEYS:
        p = ti.get(key)
        if isinstance(p, str) and p:
            return p
    return ""


def allowed_dirs():
    return [os.path.realpath(d) for d in os.environ.get(ENV_GUARD_ALLOW, "").split(os.pathsep) if d]


def deny_reason(path, main, worktree):
    """Deny text when `path` (real) is a main-checkout write this session may not make; '' otherwise."""
    if not under(path, main) or under(path, worktree):
        return ""
    if any(under(path, d) for d in allowed_dirs()):
        return ""
    return (
        f"write_guard: {path} is in the main checkout {main}; this dev session works in the job worktree {worktree}. "
        f"Write only: use a path under the worktree root {worktree}."
    )


def log_denial(tool, path):
    """Append {ts, job, tool, path} to the guard log; a failed write never breaks the hook."""
    log = os.environ.get(ENV_GUARD_LOG, "")
    if not log:
        return
    line = json.dumps({"ts": time.time(), "job": os.environ.get(ENV_GUARD_JOB, ""), "tool": tool, "path": path})
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        return


def main():
    worktree = os.environ.get(ENV_GUARD_WORKTREE, "")
    if not worktree:
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        print("write_guard: hook input is not a JSON object; the write cannot be checked.", file=sys.stderr)
        return 2
    tool = data.get("tool_name")
    if tool not in GUARD_TOOLS:
        return 0
    p = target_path(data)
    if not p:
        return 0
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
    path = real(p, cwd)
    main_env = os.environ.get(ENV_GUARD_MAIN, "")
    if not main_env:
        return 0  # no main checkout named: nothing to protect
    main = os.path.realpath(main_env)
    why = deny_reason(path, main, os.path.realpath(worktree))
    if why:
        log_denial(tool, path)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
