"""Append one JSON line per hook denial to the stage scratch dir (denials.jsonl); no-op when ENV_SCRATCH_DIR is unset."""

import json
import os
import time

from pipeline.configuration import ENV_AGENT_KIND, ENV_DENIAL_SESSION, ENV_SCRATCH_DIR

DENIALS_FILE = "denials.jsonl"  # the log name inside the stage scratch dir
COMMAND_CHARS = 200  # a denied command is cut to this many characters in the log


def append(rule, command, cwd, now=None):
    """Write {ts, rule, command, cwd, agent, session} as one line (agent: ENV_AGENT_KIND, session: ENV_DENIAL_SESSION, both empty when unset); a log write that fails never breaks the hook (the deny still stands)."""
    scratch = os.environ.get(ENV_SCRATCH_DIR, "")
    if not scratch:
        return
    agent = os.environ.get(ENV_AGENT_KIND, "")
    session = os.environ.get(ENV_DENIAL_SESSION, "")
    line = json.dumps(
        {"ts": time.time() if now is None else now, "rule": rule, "command": command[:COMMAND_CHARS], "cwd": cwd, "agent": agent, "session": session}
    )
    try:
        with open(os.path.join(scratch, DENIALS_FILE), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        return


def rows(scratch):
    """The rows of the scratch dir's denials.jsonl ({ts, rule, command, cwd, agent}) in file order; [] when the file is missing; lines that are not a JSON object are skipped."""
    try:
        with open(os.path.join(scratch, DENIALS_FILE), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for x in lines:
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out
