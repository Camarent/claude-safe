#!/usr/bin/env python3
"""Per-task cost ledger over Claude Code transcripts: lead vs subagents, per agent type,
per task (with retry rounds), and cost per completed task.

    python3 -I /root/.claude/tools/retro/ledger.py <dir-or-jsonl>... [--tasks TASKS.md] [--top N] [--metrics metrics.jsonl]

A <dir> is searched recursively for `<session>/subagents/agent-*.jsonl` (+ `.meta.json`
with agentType/description) and the lead log `<session>.jsonl` next to the session dir.

Units are WEIGHTED TOKENS, a relative measure of limit use, not dollars:
  unit = model_weight * (input + 1.25*cache_creation + 0.1*cache_read + 5*output) / 1000
The ratios (cache write 1.25x, cache read 0.1x, output 5x input) and the model weights
below are ASSUMPTIONS taken from Anthropic's published price ratios; change them in
WEIGHTS / TOKEN_RATIOS (or pass --weights file.json) if your plan counts differently.
Task = `task-N` found in the agent's description (stage word before it is kept).
`--tasks`: a TASKS.md; `- [x] task-N` lines count as completed.
"""

import argparse
import contextlib
import glob
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import prices

TOKEN_RATIOS = {"input": 1.0, "cache_creation": 1.25, "cache_read": 0.1, "output": 5.0}
WEIGHTS = {"haiku": 1.0, "sonnet": 3.0, "opus": 5.0}
TASK_RE = re.compile(r"(?:(\S+)\s+)?(task-\d+)", re.I)
DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(task-\d+)")


def weight(model, weights):
    m = (model or "").lower()
    for key, w in weights.items():
        if key in m:
            return w
    return weights.get("sonnet", 3.0)


def _read_messages(path):
    """-> ({message id: last assistant message with usage}, first timestamp, last timestamp)."""
    first = last = None
    by_id = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            ts = o.get("timestamp")
            if ts:
                first = first or ts
                last = ts
            msg = o.get("message") if o.get("type") == "assistant" else None
            if not isinstance(msg, dict) or not msg.get("usage"):
                continue
            by_id[msg.get("id") or f"anon{n}"] = msg
    return by_id, first, last


def read_log(path, weights):
    """-> dict(turns, units, tokens{...}, models{name: units}, secs).
    Claude Code logs ONE RECORD PER CONTENT BLOCK; parallel tool calls and thinking blocks of the same API message repeat that message's usage.
    So a turn is a message id, counted once (the last record of an id carries the final output_tokens)."""
    by_id, first, last = _read_messages(path)
    tok: defaultdict[str, int] = defaultdict(int)
    models: defaultdict[str, float] = defaultdict(float)
    turns, units = 0, 0.0
    usd, estimated = 0.0, set()
    for msg in by_id.values():
        u = msg["usage"]
        parts = {
            "input": int(u.get("input_tokens") or 0),
            "cache_creation": int(u.get("cache_creation_input_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "output": int(u.get("output_tokens") or 0),
        }
        if not any(parts.values()):
            continue
        turns += 1
        cost = weight(msg.get("model"), weights) * sum(TOKEN_RATIOS[k] * v for k, v in parts.items()) / 1000.0
        units += cost
        models[msg.get("model") or "?"] += cost
        priced = prices.cost(msg.get("model"), parts["input"], parts["output"], parts["cache_read"], parts["cache_creation"])
        if priced is None:
            priced = cost * prices.DERIVED_USD_PER_UNIT
            estimated.add(msg.get("model") or "?")
        usd += priced
        for k, v in parts.items():
            tok[k] += v
    return {"turns": turns, "units": units, "tokens": dict(tok), "models": dict(models), "secs": span(first, last), "usd": usd, "estimated": estimated}


def epoch(ts):
    """-> seconds since the epoch of an ISO timestamp, 0.0 for a missing or malformed one."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, OSError, OverflowError, ValueError):
        return 0.0


def span(a, b):
    from datetime import datetime

    try:

        def f(s):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        return max(0.0, (f(b) - f(a)).total_seconds())
    except (ValueError, AttributeError):
        return 0.0


def metrics_sids(path):
    """-> set of non-empty `sid` values of the `session` rows in a pipeline metrics.jsonl (empty if unreadable)."""
    out = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                with contextlib.suppress(ValueError):
                    r = json.loads(line)
                    if isinstance(r, dict) and r.get("ev") == "session" and r.get("sid"):
                        out.add(str(r["sid"]))
    except OSError:
        pass
    return out


def config_roots():
    """-> the `projects` dirs that hold session logs: ~/.claude/projects, then <CLAUDE_CONFIG_DIR>/projects when that env var is set (as retro/denials.py reads it)."""
    roots = [os.path.expanduser("~/.claude/projects")]
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        roots.append(os.path.join(cfg, "projects"))
    return roots


def find_transcript(sid, root=None):
    """-> path of <root>/*/<sid>.jsonl for the first root that holds it, or None (also for ids that are not plain tokens).
    `root`: search only this projects dir; default: every root of config_roots() (headless driver logs can sit in a config dir of their own)."""
    if not sid or not sid.replace("-", "").isalnum():
        return None
    for r in [root] if root else config_roots():
        hits = glob.glob(os.path.join(r, "*", f"{sid}.jsonl"))
        if hits:
            return hits[0]
    return None


def _read_meta(path):
    """-> parsed `.meta.json` at path, or {} when missing or not valid JSON."""
    meta = {}
    if os.path.exists(path):
        with contextlib.suppress(ValueError):
            meta = json.loads(Path(path).read_text(encoding="utf-8"))
    return meta


def _agent_logs(root, files):
    """-> [(agent_log, meta)] for the `agent-*.jsonl` files of one subagents dir."""
    agents = []
    for fn in sorted(files):
        if fn.startswith("agent-") and fn.endswith(".jsonl"):
            meta = _read_meta(os.path.join(root, fn[:-6] + ".meta.json"))
            agents.append((os.path.join(root, fn), meta))
    return agents


def _add_subagents_session(root, files, sessions):
    """Record the session that owns the subagents dir `root`; its lead log replaces a bare entry for it."""
    sdir = os.path.dirname(root)
    sid = os.path.basename(sdir)
    lead = os.path.join(os.path.dirname(sdir), sid + ".jsonl")
    sessions.pop(lead, None)
    sessions[sdir] = (sid, lead if os.path.exists(lead) else None, _agent_logs(root, files))


def _add_headless_sids(root, files, sids, sessions):
    """Add top-level `<sid>.jsonl` logs whose id is in sids as sessions of their own."""
    for fn in sorted(files):
        if sids and fn.endswith(".jsonl") and fn[:-6] in sids:
            sessions.setdefault(os.path.join(root, fn), (fn[:-6], os.path.join(root, fn), []))


def find_sessions(paths, sids=None):
    """-> list of (session_id, lead_log or None, [(agent_log, meta)]).
    `sids`: ids of a stage's headless sessions (see metrics_sids); top-level `<sid>.jsonl` logs with those ids are
    added as sessions of their own, since the driver's sessions have no subagents/ dir."""
    sessions: dict = {}
    for p in paths:
        if os.path.isfile(p):
            sessions[p] = (os.path.basename(p), p if "/subagents/" not in p else None, [])
            continue
        for root, _dirs, files in os.walk(p):
            if os.path.basename(root) == "subagents":
                _add_subagents_session(root, files, sessions)
            else:
                _add_headless_sids(root, files, sids, sessions)
    return list(sessions.values())


def task_key(desc):
    m = TASK_RE.search(desc or "")
    if not m:
        return None
    stage = (m.group(1) or "").strip(":,;")
    key = m.group(2).lower()
    return f"{stage}/{key}" if stage and not stage.lower().startswith("task") else key


def done_tasks(path):
    if not path:
        return set()
    with open(path, encoding="utf-8", errors="replace") as f:
        return {m.group(1).lower() for line in f for m in [DONE_RE.match(line)] if m}


SKIPPED: list = []


def safe_read(path, weights):
    try:
        return read_log(path, weights)
    except OSError:
        SKIPPED.append(path)
        return {"turns": 0, "units": 0.0, "tokens": {}, "models": {}, "secs": 0.0, "usd": 0.0, "estimated": set()}


def build(paths, weights, tasks_md=None, sids=None):
    """`sids`: headless session ids of the stage (metrics_sids); same meaning as in find_sessions, so the figures match retro_numbers.analyse."""
    lead_units = lead_turns = 0
    by_type: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "turns": 0, "units": 0.0})
    by_task: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "units": 0.0, "types": [], "models": set()})
    sub_units = 0.0
    usd, estimated = 0.0, set()
    for _sid, lead, agents in find_sessions(paths, sids):
        if lead:
            r = safe_read(lead, weights)
            lead_units += r["units"]
            lead_turns += r["turns"]
            usd += r["usd"]
            estimated |= r["estimated"]
        for path, meta in agents:
            r = safe_read(path, weights)
            usd += r["usd"]
            estimated |= r["estimated"]
            t = by_type[meta.get("agentType") or "?"]
            t["runs"] += 1
            t["turns"] += r["turns"]
            t["units"] += r["units"]
            sub_units += r["units"]
            key = task_key(meta.get("description"))
            if key:
                k = by_task[key]
                k["runs"] += 1
                k["units"] += r["units"]
                k["types"].append(meta.get("agentType") or "?")
                k["models"].update(m.split("-")[1] if "-" in m else m for m in r["models"])
    done = done_tasks(tasks_md)
    return {
        "lead_units": lead_units,
        "lead_turns": lead_turns,
        "sub_units": sub_units,
        "by_type": dict(by_type),
        "by_task": dict(by_task),
        "done": done,
        "usd": usd,
        "estimated": estimated,
    }


def fmt(n):
    return f"{n / 1000:.1f}k" if abs(n) >= 1000 else f"{n:.0f}"


def _done_lines(tasks, done):
    d = [k for key, k in tasks.items() if key.split("/")[-1] in done]
    o = [k for key, k in tasks.items() if key.split("/")[-1] not in done]
    du = sum(k["units"] for k in d)
    out = ["4. Cost per completed task"]
    out.append(
        f"   {len(d)} done: {fmt(du)} units, avg {fmt(du / len(d)) if d else '-'}, avg runs {sum(k['runs'] for k in d) / len(d):.1f}"
        if d
        else "   no completed tasks matched"
    )
    out.append(f"   {len(o)} open/unmatched: {fmt(sum(k['units'] for k in o))} units spent without completion")
    return out


def render(res, top):
    out = []
    total = res["lead_units"] + res["sub_units"]

    def pct(x):
        return f"{100 * x / total:.0f}%" if total else "-"

    out.append(f"1. Lead vs subagents (weighted units, {fmt(total)} total)")
    out.append(
        f"   lead {fmt(res['lead_units'])} ({pct(res['lead_units'])}, {res['lead_turns']} turns); subagents {fmt(res['sub_units'])} ({pct(res['sub_units'])})"
    )
    out.append("2. Per agent type: runs, turns, units")
    for name, t in sorted(res["by_type"].items(), key=lambda kv: -kv[1]["units"])[:top]:
        out.append(f"   {name:<16} {t['runs']:>4} runs {t['turns']:>5} turns {fmt(t['units']):>8}")
    tasks = res["by_task"]
    out.append(f"3. Per task: runs (= rounds incl. reviews), units — top {top}")
    for key, k in sorted(tasks.items(), key=lambda kv: -kv[1]["units"])[:top]:
        mark = ("done" if key.split("/")[-1] in res["done"] else "open") if res["done"] else ""
        out.append(f"   {key:<24} {k['runs']:>3} runs {fmt(k['units']):>8}  {mark:<4} {','.join(sorted(k['models']))}")
    if res["done"]:
        out += _done_lines(tasks, res["done"])
    if SKIPPED:
        out.append(f"   NOTE: {len(SKIPPED)} unreadable transcripts skipped (root-owned; run the ledger inside the container to include them)")
    out.append(f"USD (price table of {prices.PRICES_DATE}, re-check it): ${res['usd']:.2f}")
    if res["estimated"]:
        out.append(f"   ESTIMATE: no price row for {', '.join(sorted(res['estimated']))}; those turns use the derived rate (one Opus run), not a price")
    out.append("Weights are assumptions (see ledger.py header); compare runs, do not read units as money.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--tasks", help="TASKS.md; `- [x] task-N` = completed")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--weights", help="JSON file: {haiku, sonnet, opus} weights")
    ap.add_argument("--metrics", help="metrics.jsonl of the stage: its headless session ids are counted as sessions too")
    a = ap.parse_args(argv)
    weights = dict(WEIGHTS)
    if a.weights:
        weights.update(json.loads(Path(a.weights).read_text(encoding="utf-8")))
    sids = metrics_sids(a.metrics) if a.metrics else None
    print(render(build(a.paths, weights, a.tasks, sids), a.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
