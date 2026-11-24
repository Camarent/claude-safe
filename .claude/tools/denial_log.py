"""Append one JSON line per hook denial to the stage scratch dir (denials.jsonl); no-op when ENV_SCRATCH_DIR is unset."""

import json
import os
import time

from pipeline.configuration import ENV_AGENT_KIND, ENV_SCRATCH_DIR

DENIALS_FILE = "denials.jsonl"  # the log name inside the stage scratch dir
COMMAND_CHARS = 200  # a denied command is cut to this many characters in the log


def append(rule, command, cwd, now=None):
    """Write {ts, rule, command, cwd, agent} as one line (agent: ENV_AGENT_KIND, empty when unset); a log write that fails never breaks the hook (the deny still stands)."""
    scratch = os.environ.get(ENV_SCRATCH_DIR, "")
    if not scratch:
        return
    agent = os.environ.get(ENV_AGENT_KIND, "")
    line = json.dumps({"ts": time.time() if now is None else now, "rule": rule, "command": command[:COMMAND_CHARS], "cwd": cwd, "agent": agent})
    try:
        with open(os.path.join(scratch, DENIALS_FILE), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        return
