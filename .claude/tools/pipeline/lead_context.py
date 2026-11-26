"""Lead context size warning: reads the newest lead transcript, fails silent."""

import json
import os
import re
import sys
from pathlib import Path

LIMIT = 150_000
TAIL = 1_000_000


def project_dir(cwd, home=None) -> Path:
    """Claude Code's transcript folder for a cwd: the absolute path with / and . replaced by -, under ~/.claude/projects."""
    return Path(home or Path.home()) / ".claude" / "projects" / re.sub(r"[/.]", "-", os.path.abspath(cwd))


PROJECTS = project_dir(os.getcwd())


def warning(tokens, limit=LIMIT) -> str | None:
    """Return the warning line when tokens > limit, else None."""
    if tokens is None or tokens <= limit:
        return None
    return f"lead context high: {tokens // 1000}k tokens, hand off and start the next phase fresh"


def last_context_tokens(projects=PROJECTS) -> int | None:
    """Context size of the last assistant turn in the newest transcript, or None."""
    try:
        files = sorted(Path(projects).glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            return None
        with open(files[-1], "rb") as f:  # only the tail: transcripts reach tens of MB and this runs on every driver call
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL))
            lines = f.read().decode(errors="replace").splitlines()
        for line in reversed(lines[1:] if len(lines) > 1 else lines):  # the first line of a tail may be cut
            if '"usage"' not in line:
                continue
            try:
                u = json.loads(line).get("message", {}).get("usage")
            except ValueError:
                continue
            if isinstance(u, dict):
                return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return None
    except (OSError, ValueError, AttributeError):
        return None


def check(projects=PROJECTS, out=None) -> None:
    """Print the warning to stderr when the lead context is high. Never raises."""
    line = warning(last_context_tokens(projects))
    if line:
        print(line, file=out or sys.stderr, flush=True)
