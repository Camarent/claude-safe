"""Lead-session cost lines for the retro: USD split by token kind, wall vs active time, cache-expiry gaps.

    python3 -I /root/.claude/tools/retro/lead_cost.py <lead.jsonl>...

`retro_numbers.py` calls `lead_lines` for one stage, `feature_retro.py` calls `feature_lines` for a whole feature (all phases).
A script, no model: the numbers are exact sums over the transcripts; prices come from `pipeline/prices.py` (the only place prices live).

A lead is the interactive session (entrypoint `cli`) that runs wave.py / plan.py / orch.py; headless `sdk-cli` sessions are the
pipeline's own agents and already in metrics.jsonl. The transcripts do not name their project, so a lead belongs to a stage or feature by TIME: only turns inside the
window (first to last `t` in the orch.jsonl files) count. A second project's lead running at the same time would be counted too.

A *gap* is the time between two consecutive assistant turns (a long tool run counts too: the prompt cache expires either way).
After a gap longer than `CACHE_TTL_S` the context is written to the cache again instead of read; `rewrite` is the extra cost of
that: cache-write tokens of the turn after the gap, priced as write minus read. The TTL of the real requests is not in the log
(5 min or 1 h); `1h tokens` shows how many cache-write tokens the log marks as 1-hour writes, when it says so.
"""

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import prices
from pipeline.configuration import METRICS_LOG, ORCH_LOG

from retro import ledger

CACHE_TTL_S = 300
TOP_SESSIONS = 5  # sessions printed one by one (by cost); the total line covers all
LONG_GAP_S = 3600
HEADLESS_ENTRYPOINT = "sdk-cli"
# the lead is the session that runs these; ops_steps = the ops sessions (cleanup, push, digest)
PIPELINE_TOOLS = ("wave.py", "plan.py", "orch.py", "ops_steps.py")
KINDS = (("cache write", "cache_write"), ("cache read", "cache_read"), ("fresh input", "input"), ("output", "output"))


def _turns(path):
    """-> [(epoch, model, usage dict)] one entry per assistant message id, in file order; the last record of an id carries the final usage."""
    by_id, order = {}, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            msg = o.get("message") if isinstance(o, dict) and o.get("type") == "assistant" else None
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            key = msg.get("id") or f"anon{n}"
            if key not in by_id:
                order.append((key, ledger.epoch(o["timestamp"]) if o.get("timestamp") else 0.0))
            by_id[key] = msg
    return [(ts, by_id[k].get("model") or "", by_id[k]["usage"]) for k, ts in order]


def _parts(u):
    return {
        "input": int(u.get("input_tokens") or 0),
        "cache_write": int(u.get("cache_creation_input_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
    }


def _usd(model, parts):
    """-> {kind: USD} for one turn, or None when the model has no price row."""
    p = prices.lookup(model)
    if p is None:
        return None
    return {k: parts[k] * p[k] / 1e6 for k in parts}


def analyse_lead(path, lo=0.0, hi=float("inf")):
    """-> dict for one lead transcript, turns inside [lo, hi] only: usd and tokens per kind, turns, wall/active seconds, gap counts, rewrite cost, peak context."""
    usd = dict.fromkeys(("input", "cache_write", "cache_read", "output"), 0.0)
    tok = dict.fromkeys(usd, 0)
    r: dict[str, Any] = {
        "turns": 0,
        "unpriced": 0,
        "gaps": 0,
        "long_gaps": 0,
        "rewrite": 0.0,
        "rewrite_tokens": 0,
        "active": 0.0,
        "wall": 0.0,
        "peak": 0,
        "h1": 0,
    }
    prev = first = None
    for ts, model, u in _turns(path):
        parts = _parts(u)
        if not any(parts.values()) or not lo <= ts <= hi:
            continue
        r["turns"] += 1
        r["peak"] = max(r["peak"], sum(parts[k] for k in ("input", "cache_write", "cache_read")))
        r["h1"] += int((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
        for k, v in parts.items():
            tok[k] += v
        cost = _usd(model, parts)
        if cost is None:
            r["unpriced"] += 1
        else:
            for k, v in cost.items():
                usd[k] += v
        if ts:
            first = first or ts
            if prev:
                gap = ts - prev
                if gap <= CACHE_TTL_S:
                    r["active"] += gap
                else:
                    r["gaps"] += 1
                    r["long_gaps"] += gap > LONG_GAP_S
                    p = prices.lookup(model)
                    if p:
                        r["rewrite"] += parts["cache_write"] * (p["cache_write"] - p["cache_read"]) / 1e6
                        r["rewrite_tokens"] += parts["cache_write"]
            r["wall"] = ts - first
            prev = ts
    r.update(usd=usd, tok=tok)
    return r


def _hours(s):
    return f"{s / 3600:.1f}h" if s >= 3600 else f"{s / 60:.0f}min"


def _split(r):
    total = sum(r["usd"].values()) or 1.0
    return ", ".join(f"{name} ${r['usd'][k]:.2f} ({100 * r['usd'][k] / total:.0f}%)" for name, k in KINDS)


def session_line(sid, r):
    total = sum(r["usd"].values())
    active = f"{100 * r['active'] / r['wall']:.0f}%" if r["wall"] else "n/a"
    flag = f" [{r['unpriced']} turns on an unpriced model, left out]" if r["unpriced"] else ""
    h1 = f", 1h tokens {r['h1'] // 1000}k" if r["h1"] else ""
    return (
        f"LEAD-COST {sid[:8]}: ${total:.2f}, {r['turns']} turns, wall {_hours(r['wall'])}, active {active}, peak ctx {r['peak'] // 1000}k{flag}\n"
        f"   {_split(r)}\n"
        f"   gaps > {CACHE_TTL_S // 60} min: {r['gaps']} ({r['long_gaps']} > 1h); cache rewrites after them {r['rewrite_tokens'] // 1000}k tokens, "
        f"extra ${r['rewrite']:.2f} over reading them{h1}"
    )


def lead_lines(paths, lo=0.0, hi=float("inf")):
    """-> retro lines for the lead transcripts `paths`, turns inside [lo, hi]: the costliest sessions, then a total; [] without lead turns."""
    res = [(os.path.basename(p)[:-6] if p.endswith(".jsonl") else os.path.basename(p), analyse_lead(p, lo, hi)) for p in paths]
    res = [(s, r) for s, r in res if r["turns"]]
    if not res:
        return []
    ranked = sorted(res, key=lambda x: -sum(x[1]["usd"].values()))
    out = [session_line(s, r) for s, r in ranked[:TOP_SESSIONS]]
    tot = {k: sum(r["usd"][k] for _, r in res) for k in res[0][1]["usd"]}
    all_r = {"usd": tot}
    out.append(
        f"LEAD-COST total: ${sum(tot.values()):.2f} in {len(res)} session(s): {_split(all_r)}; "
        f"gaps > {CACHE_TTL_S // 60} min {sum(r['gaps'] for _, r in res)}, rewrite extra ${sum(r['rewrite'] for _, r in res):.2f}"
    )
    return out


def _t(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                stamp = o.get("t", o.get("ts")) if isinstance(o, dict) else None  # orch.jsonl `t`, metrics.jsonl `ts`
                if isinstance(stamp, (int, float)):
                    out.append(float(stamp))
    except OSError:
        pass
    return out


def window(orch_paths):
    """-> (first, last) time over the orch.jsonl (`t`) and metrics.jsonl (`ts`) files, (0, 0) when none has events. Metrics rows count: the snapshot after the last agent comes after the last orch event."""
    ts = [t for p in orch_paths for t in _t(p)]
    return (min(ts), max(ts)) if ts else (0.0, 0.0)


def _line_commands(o):
    """-> the Bash commands (str) of one transcript object: its assistant message's Bash tool_use blocks."""
    msg = o.get("message") if o.get("type") == "assistant" else None
    blocks = (msg.get("content") if isinstance(msg, dict) else None) or []
    cmds = [(b.get("input") or {}).get("command") for b in blocks if isinstance(b, dict) and b.get("name") == "Bash"]
    return [c for c in cmds if isinstance(c, str)]


def _entries(path):
    """-> (entrypoint, bash command) per Bash command of the transcript; (entrypoint, None) for a line with none. Non-object lines are skipped."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"Bash"' not in line and '"entrypoint"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            ep = o.get("entrypoint")
            for cmd in _line_commands(o) or [None]:
                yield ep, cmd


def _runs_pipeline_tool(cmd):
    """True when a Bash command names one of PIPELINE_TOOLS."""
    return isinstance(cmd, str) and any(t in cmd for t in PIPELINE_TOOLS)


def is_lead(path):
    """True for a lead: an interactive session (entrypoint `cli`, not the headless `sdk-cli`) that ran one of PIPELINE_TOOLS in a Bash call."""
    interactive = False
    for ep, cmd in _entries(path):
        if ep:
            if ep == HEADLESS_ENTRYPOINT:
                return False
            interactive = True
        if interactive and _runs_pipeline_tool(cmd):
            return True
    return False


def lead_paths(projects):
    """-> transcripts of the leads under `projects` (files or folders; subagent logs are skipped)."""
    out = []
    for p in projects:
        if os.path.isfile(p):
            out.append(p)
            continue
        for root, _dirs, files in os.walk(p):
            if "subagents" not in root.split(os.sep):
                out += [os.path.join(root, f) for f in sorted(files) if f.endswith(".jsonl")]
    return [p for p in out if is_lead(p)]


def _agent_logs(lead_path):
    """-> the subagents/agent-*.jsonl logs of the lead transcript LEAD_PATH (`<dir>/<sid>.jsonl` -> `<dir>/<sid>/subagents/`)."""
    sub = os.path.join(lead_path[: -len(".jsonl")], "subagents")
    return [os.path.join(sub, f) for f in sorted(os.listdir(sub)) if f.startswith("agent-") and f.endswith(".jsonl")] if os.path.isdir(sub) else []


def _agent_type(log):
    """-> the agentType of an agent log's .meta.json, "" when it has none."""
    try:
        with open(log[: -len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
            return str(json.load(f).get("agentType") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def lead_agent_rows(paths, lo, hi):
    """-> (rows, unmatched): one row per agent log under the subagents/ of a lead that has turns in [lo, hi], when every turn of the agent log lies inside the window.
    Rows: sid (the lead's), agent (log name), type, turns, cost_usd, usd. A log with no timestamped turn or a turn outside the window is not guessed at: it is counted in `unmatched`."""
    rows, unmatched = [], 0
    for p in lead_paths(paths):
        if not analyse_lead(p, lo, hi)["turns"]:
            continue
        for log in _agent_logs(p):
            stamps = [ts for ts, _m, u in _turns(log) if any(_parts(u).values())]
            if not stamps or not all(ts and lo <= ts <= hi for ts in stamps):
                unmatched += 1
                continue
            r = analyse_lead(log, lo, hi)
            rows.append(
                {
                    "sid": os.path.basename(p)[: -len(".jsonl")],
                    "agent": os.path.basename(log)[: -len(".jsonl")],
                    "type": _agent_type(log),
                    "turns": r["turns"],
                    "cost_usd": round(sum(r["usd"].values()), 4),
                    "usd": r["usd"],
                }
            )
    return rows, unmatched


def _tot(paths, lo, hi):
    rs = [analyse_lead(p, lo, hi) for p in paths]
    rs = [r for r in rs if r["turns"]]
    usd = {k: sum(r["usd"][k] for r in rs) for k in dict(KINDS).values()}
    return rs, {"usd": usd}


def _phases(feature_dir):
    """-> (phase folder names sorted, their orch.jsonl paths) of .work/<feature>/phases."""
    root = os.path.join(feature_dir, "phases")
    phases = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))) if os.path.isdir(root) else []
    return phases, [os.path.join(root, d, ORCH_LOG) for d in phases]


def _total_lines(feature_dir, paths, rs, tot, lo, hi):
    """-> the feature's total LEAD-COST lines (header, split, outside-window and gap lines) over the sessions RS in the window LO..HI."""
    gaps, rew = sum(r["gaps"] for r in rs), sum(r["rewrite"] for r in rs)
    wall, act = sum(r["wall"] for r in rs), sum(r["active"] for r in rs)
    return [
        (
            f"LEAD-COST feature {os.path.basename(os.path.normpath(feature_dir))}: ${sum(tot['usd'].values()):.2f} in {len(rs)} lead session(s), "
            f"{sum(r['turns'] for r in rs)} turns, wall {_hours(wall)}, active {100 * act / wall if wall else 0:.0f}%, peak ctx {max(r['peak'] for r in rs) // 1000}k"
        ),
        f"   {_split(tot)}",
        f"   whole transcript(s) of these leads, outside the window too: ${sum(sum(analyse_lead(p)['usd'].values()) for p in paths if analyse_lead(p, lo, hi)['turns']):.2f}",
        f"   gaps > {CACHE_TTL_S // 60} min: {gaps}; cache rewrites after them extra ${rew:.2f} over reading them (window includes time between phases)",
    ]


def _phase_line(d, o, paths):
    """-> the LEAD-COST line of phase folder D (its orch.jsonl O) over the lead sessions in PATHS."""
    plo, phi = window([o])
    prs, ptot = _tot(paths, plo, phi) if phi else ([], {"usd": {}})
    return f"   phase {d}: ${sum(ptot['usd'].values()):.2f}, {sum(r['turns'] for r in prs)} turns"


def feature_lines(feature_dir, projects):
    """-> LEAD-COST lines for a whole feature: one total over the window of all phases, then one line per phase. [] without lead turns.
    `feature_dir` is .work/<feature> with phases/<n>/orch.jsonl (+ metrics.jsonl)."""
    phases, orchs = _phases(feature_dir)
    paths = lead_paths(projects)
    lo, hi = window(orchs + [os.path.join(os.path.dirname(o), METRICS_LOG) for o in orchs])
    if not hi:
        return []
    rs, tot = _tot(paths, lo, hi)
    if not rs:
        return []
    out = _total_lines(feature_dir, paths, rs, tot, lo, hi)
    out += [_phase_line(d, o, paths) for d, o in zip(phases, orchs, strict=True)]
    return out


def main(argv=None):
    paths = argv if argv is not None else sys.argv[1:]
    print("\n".join(lead_lines(paths)) or "LEAD-COST: no lead turns")


if __name__ == "__main__":
    main()
