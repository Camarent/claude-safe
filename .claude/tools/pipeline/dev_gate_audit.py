#!/usr/bin/env python3
"""Warn on a dev session's full-gate, repeated or slow gate runs, read from its tool-call log (transcript JSONL).

    python3 -I .claude/tools/pipeline/dev_gate_audit.py <stage> <task> [--dir <workspace>]

Three warning kinds, one line each:
- a Bash call that runs a full gate command (pipeline.json `dev_gate_full_cmds`);
- the same full gate command run more than `dev_gate_repeat_max` times in one session;
- a Bash call whose tool_use to tool_result timestamps differ by more than the `unit` limit of `gate_time_warn_s`.

Only warnings: never a verdict, never blocks. A missing metrics file or transcript gives no lines; an unreadable line is skipped.
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import ledger
from retro.agent_cost import load

from pipeline.configuration import (
    DEV_GATE_FULL_CMDS_KEY,
    DEV_GATE_REPEAT_MAX_KEY,
    EV_SESSION,
    GATE_TIME_WARN_KEY,
    METRICS_LOG,
    WORK_DIR,
    ConfigError,
    project,
)
from pipeline.util import read_jsonl

CMD_SHOWN = 80  # characters of a command shown in a warning line


def _shown(cmd):
    return " ".join(cmd.split())[:CMD_SHOWN]


def _full_cmd_of(cmd, full_cmds):
    """-> the configured full-gate command that CMD runs as a whole word, or None."""
    for full in full_cmds:
        if re.search(rf"(?<!\S){re.escape(full)}(?!\S)", cmd):
            return full
    return None


def _bash_calls(records):
    """-> {tool_use id: (command, timestamp)} of the Bash tool_use blocks of the assistant records."""
    calls = {}
    for rec in records:
        msg = rec.get("message")
        if rec.get("type") != "assistant" or not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        for blk in msg["content"]:
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                inp = blk.get("input")
                inp = inp if isinstance(inp, dict) else {}
                calls[blk.get("id")] = (str(inp.get("command", "")), rec.get("timestamp"))
    return calls


def _slow_lines(records, calls, unit_limit_s):
    """-> warning lines for Bash calls whose result came more than unit_limit_s seconds after the call."""
    lines = []
    for rec in records:
        msg = rec.get("message")
        if rec.get("type") != "user" or not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        for blk in msg["content"]:
            hit = calls.get(blk.get("tool_use_id")) if isinstance(blk, dict) and blk.get("type") == "tool_result" else None
            if not hit:
                continue
            cmd, started = hit
            secs = ledger.epoch(rec.get("timestamp")) - ledger.epoch(started)
            if ledger.epoch(started) and secs > unit_limit_s:
                lines.append(f"dev gate: slow Bash call {secs:.0f}s over {unit_limit_s}s: `{_shown(cmd)}`")
    return lines


def audit(records, full_cmds, repeat_max, unit_limit_s):
    """-> warning lines for one dev session's transcript records (dicts, as retro.agent_cost.load returns them)."""
    calls = _bash_calls(records)
    lines = []
    runs: Counter[str] = Counter()
    for cmd in [c[0] for c in calls.values()]:
        full = _full_cmd_of(cmd, full_cmds)
        if full is not None:
            runs[full] += 1
            lines.append(f"dev gate: full gate `{_shown(cmd)}` run by a dev")
    for full, n in runs.items():
        if n > repeat_max:
            lines.append(f"dev gate: `{full}` run {n} times (max {repeat_max})")
    lines += _slow_lines(records, calls, unit_limit_s)
    return lines


def warn_task(d, stage, task):
    """-> warning lines of the dev sessions of TASK in stage STAGE of workspace D; never raises, [] when nothing can be read."""
    try:
        cfg = project(d)
        rows = read_jsonl(os.path.join(d, WORK_DIR, stage, METRICS_LOG), quiet=True, errors="replace")
        sids = sorted({str(r["sid"]) for r in rows if r.get("ev") == EV_SESSION and r.get("agent") == "dev" and r.get("task") == task and r.get("sid")})
        lines = []
        for sid in sids:
            path = ledger.find_transcript(sid)
            if not path:
                continue
            records = load(path)[0]
            lines += audit(records, cfg[DEV_GATE_FULL_CMDS_KEY], cfg[DEV_GATE_REPEAT_MAX_KEY], cfg[GATE_TIME_WARN_KEY]["unit"])
        return lines
    except (OSError, ValueError, KeyError, TypeError, ConfigError):
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Warn on a dev's full-gate, repeated or slow gate runs (warnings only).")
    ap.add_argument("stage")
    ap.add_argument("task")
    ap.add_argument("--dir", default=os.getcwd(), help="workspace holding pipeline.json and the stage work dir")
    a = ap.parse_args(argv)
    for line in warn_task(a.dir, a.stage, a.task):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
