#!/usr/bin/env python3
"""Transcript numbers for the stage retro (thresholds agreed with the owner 2026-10-27, practices/orch-pipeline.md "Retro").

    python3 -I /root/.claude/tools/retro/retro_numbers.py [DIR-or-jsonl ...] [--orch .work/S/orch.jsonl] [--out FILE]

DIR defaults to <HOME>/projects (HOME from pipeline/configuration/paths.py). --orch limits the logs to those that started after the stage's first event (orch.jsonl events carry `t`).
Prints `RETRO <signal>: <numbers>` for every tripped signal (or one `RETRO-NUMBERS: nothing tripped` line), then a short table.
Context = input + cache_creation + cache_read tokens of one assistant message (the prompt size of that turn); peak per log.
Cost = ledger.py weighted units. One turn = one message id (Claude Code logs one record per content block)."""

import argparse
import collections
import contextlib
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import DIGEST_TOTAL_RE, HOME, LEAD_AGENT_EV, LEAD_AGENT_UNMATCHED_EV, LEAD_EV, METRICS_LOG, MODEL_SONNET, SEARCH_TOOL_NAMES
from pipeline.metrics import session_totals
from pipeline.shellwords import starts_command
from pipeline.util import append_jsonl, now, read_jsonl

from retro import lead_cost, ledger

LEAD_CTX, AGENT_CTX = 150_000, 100_000
TURN_CAPS = {"dev": 40, "reviewer": 40, "tester": 40, "web-tester": 40, "sec-auditor": 100, "arch-auditor": 100, "ux-reviewer": 100, "architect": 100}
HEAVY = ("sec-auditor", "arch-auditor", "ux-reviewer", "architect")
HEAVY_SHARE, TASK_MULT = 0.40, 3.0
BIG_READ_CHARS, BIG_READS = 30_000, 3  # whole-file Read (no offset/limit) with a result above this; trips at this many per transcript
GREP_MIN = 10  # grep/rg calls in one transcript before the local.py comparison matters
GREP_NAMES = ("grep", "rg", "egrep")  # command words that count as a grep call
WAVE_MULT, WAVE_MIN = 2.0, 3  # wave-cost: a wave above this x the median wave cost, needs this many waves
WAVE_FLOOR_USD = 0.25  # wave-cost: cheaper waves are never flagged (with `depends` a "wave" is one scheduler tick worth cents, so the ratio alone is noise)
NOTOOLS_SHARE = 0.5  # tools-missing: more than this share of sessions without tool data
LP_RE = "(?:" + "|".join(rf"(?:^|[\s;|&(/]){re.escape(n)}" for n in SEARCH_TOOL_NAMES) + ")"  # the tool by any name in SEARCH_TOOL_NAMES
CENT = 0.005  # digest-stale: USD difference that counts as a mismatch
NAV_SHARE = 0.3  # goal: at least this share of search calls (local.py vs grep) goes through local.py


CTX_SLOW_S = 300  # context step longer than this trips context-slow (owner 2026-11-01: ideal under 300 s, hard limit 600 s)
CTX_RECALL_MIN = 0.3  # modified files found in plan-context.md (owner 2026-11-01: 30% is the bar); judged only with CTX_RECALL_MIN_FILES or more modified files
CTX_RECALL_MIN_FILES = 5


epoch = ledger.epoch


def _count_search(cmd, name, c):
    """Count one tool_use toward the grep / local.py nav / offload tallies in `c`."""
    if name == "Grep":
        c["grep"] += 1
    if name == "Bash" and isinstance(cmd, str):
        if re.search(LP_RE, cmd):
            if re.search(LP_RE + r"\s+(judge|review|bulk|pack|code|log)\b", cmd):
                c["offload"] += 1  # work moved to the local model: no paid tokens
            else:
                c["nav"] += 1
        elif starts_command(cmd, GREP_NAMES):
            c["grep"] += 1


def _result_size(c):
    if isinstance(c, str):
        return len(c)
    return sum(len(x.get("text") or "") for x in c if isinstance(x, dict)) if isinstance(c, list) else 0


def _scan_block(b, uses, big_reads, c):
    """One content block of a transcript record: remember tool_use inputs, tally searches, collect whole-file Reads of big results."""
    if b.get("type") == "tool_use":
        inp = b.get("input") or {}
        uses[b.get("id")] = (b.get("name"), inp)
        _count_search(inp.get("command") if isinstance(inp, dict) else None, b.get("name"), c)
    elif b.get("type") == "tool_result":
        name, inp = uses.get(b.get("tool_use_id"), (None, {}))
        size = _result_size(b.get("content"))
        if name == "Read" and size > BIG_READ_CHARS and not (inp.get("offset") or inp.get("limit")):
            big_reads.append((inp.get("file_path"), size))


def _blocks(o):
    """The dict content blocks of a transcript record."""
    content = (o.get("message") or {}).get("content") if isinstance(o.get("message"), dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _prompt_size(u):
    return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def scan(path):
    """-> dict(turns, peak_ctx, units, first) for one transcript."""
    by_id, first = {}, None
    uses: dict = {}
    big_reads: list = []
    c = {"grep": 0, "nav": 0, "offload": 0}
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            first = first or o.get("timestamp")
            for b in _blocks(o):
                _scan_block(b, uses, big_reads, c)
            msg = o.get("message") if o.get("type") == "assistant" else None
            if isinstance(msg, dict) and msg.get("usage"):
                by_id[msg.get("id") or f"anon{n}"] = msg
    peak = max((_prompt_size(m["usage"]) for m in by_id.values()), default=0)
    r = ledger.read_log(path, ledger.WEIGHTS)
    return {
        "turns": r["turns"],
        "peak_ctx": peak,
        "units": r["units"],
        "first": epoch(first) if first else 0.0,
        "big_reads": big_reads,
        "grep": c["grep"],
        "nav": c["nav"],
        "offload": c["offload"],
    }


def explore_signals(who, r):
    """Whole-file Reads of big files, and grep/rg use compared with local.py (code-nav.md)."""
    out = []
    if len(r["big_reads"]) >= BIG_READS:
        top = ", ".join(f"{os.path.basename(f or '?')} {n // 1000}k chars" for f, n in sorted(r["big_reads"], key=lambda x: -x[1])[:3])
        out.append(
            ("big-reads", f"{who} {len(r['big_reads'])} whole-file Reads above {BIG_READ_CHARS // 1000}k chars (top: {top}); use Read offset/limit or scout")
        )
    if r["grep"] >= GREP_MIN and r["nav"] * 2 < r["grep"]:
        out.append(
            ("explore", f"{who} grep/rg {r['grep']} calls vs local.py {r['nav']}; compare: would `local.py nav|find` have answered faster? (code-nav.md)")
        )
    return out


def role_ratios(rows):
    """'dev 2/12 (17%), lead 0/5 (0%)': local.py nav calls / all search calls per role; roles without search calls are left out."""
    agg: dict = {}
    for kind, _, r in rows:
        a = agg.setdefault(kind, [0, 0])
        a[0] += r["nav"]
        a[1] += r["grep"] + r["nav"]
    return ", ".join(f"{k} {a}/{t} ({a / t:.0%})" for k, (a, t) in sorted(agg.items()) if t) or "none"


def explore_ratio(rows):
    """Stage-wide share of first-search calls that went through local.py (it saves tokens, not time: raw grep hits fill the context)."""
    g, n, o = (sum(r[k] for _, _, r in rows) for k in ("grep", "nav", "offload"))
    if g + n >= GREP_MIN and n / (g + n) < NAV_SHARE:
        return [
            (
                "explore-ratio",
                f"local.py find/nav {n} of {g + n} search calls ({n / (g + n):.0%}, goal >= {NAV_SHARE:.0%}), {o} offload calls (judge/review/bulk/pack/code); per role {role_ratios(rows)}; raw grep hits cost context tokens: check the NAV sentence in the prompts, and which roles grep most",
            )
        ]
    return []


def stage_start(orch_path):
    if not orch_path or not os.path.exists(orch_path):
        return 0.0
    ts = [t for t in (r.get("t") for r in _json_lines(orch_path)) if t]
    return min(ts) if ts else 0.0


def _lead_signals(sid, r):
    sig = explore_signals(f"lead session {sid}", r)
    if r["peak_ctx"] > LEAD_CTX:
        sig.append(("context", f"lead peak context {r['peak_ctx'] // 1000}k tokens (> {LEAD_CTX // 1000}k) in session {sid}"))
    return sig


def _agent_signals(kind, meta, r):
    who = f"{kind} '{(meta.get('description') or '')[:40]}'"
    sig = explore_signals(who, r)
    if r["peak_ctx"] > AGENT_CTX:
        sig.append(("context", f"{who} peak context {r['peak_ctx'] // 1000}k tokens (> {AGENT_CTX // 1000}k)"))
    cap = TURN_CAPS.get(kind)
    if cap and r["turns"] > cap:
        sig.append(("turns", f"{who} {r['turns']} turns (> {cap})"))
    return sig


def _cost_shape_signals(total, heavy, by_task):
    sig = []
    if total and heavy / total > HEAVY_SHARE:
        sig.append(("cost-shape", f"audits + architect = {heavy / total:.0%} of weighted tokens (> {HEAVY_SHARE:.0%})"))
    if len(by_task) >= 3:
        med = statistics.median(by_task.values())
        for k, v in sorted(by_task.items(), key=lambda kv: -kv[1]):
            if med and v > TASK_MULT * med:
                sig.append(("cost-shape", f"{k} costs {v / med:.1f}x the median task"))
    return sig


def _before(r, since):
    """True when the transcript's first event is older than since (a cut-off of 0 keeps everything)."""
    return bool(since and r["first"] and r["first"] < since)


def _add_lead(sid, lead, since, rows, sig):
    """-> units of the lead log, or 0.0 when it is older than since (it still owns its in-stage subagents)."""
    r = scan(lead)
    if _before(r, since):  # an old lead still owns its in-stage subagents
        return 0.0
    rows.append(("lead", "-", r))
    sig.extend(_lead_signals(sid, r))
    return r["units"]


def analyse(paths, since=0.0, sids=None):
    sig: list[tuple[str, str]] = []
    rows: list[tuple[str, str, dict]] = []
    total, heavy = 0.0, 0.0
    by_task: dict = {}
    for sid, lead, agents in ledger.find_sessions(paths, sids):
        if lead:
            total += _add_lead(sid, lead, since, rows, sig)
        for path, meta in agents:
            r = scan(path)
            if _before(r, since):
                continue
            kind = meta.get("agentType") or "?"
            rows.append((kind, meta.get("description") or "", r))
            total += r["units"]
            heavy += r["units"] if kind in HEAVY else 0.0
            sig.extend(_agent_signals(kind, meta, r))
            key = ledger.task_key(meta.get("description"))
            if key:
                by_task[key] = by_task.get(key, 0.0) + r["units"]
    sig.extend(explore_ratio(rows))
    sig.extend(_cost_shape_signals(total, heavy, by_task))
    return sig, rows, total


COLLAPSED_SIGNALS = ("explore",)  # one line per session otherwise: render one line with the count


def collapse(sig):
    """Repeated per-session lines of a COLLAPSED_SIGNALS name -> one line: 'N sessions (first: ...)' at the first one's place."""
    counts = collections.Counter(n for n, _ in sig if n in COLLAPSED_SIGNALS)
    out, shown = [], set()
    for n, t in sig:
        if n not in COLLAPSED_SIGNALS:
            out.append((n, t))
        elif n not in shown:
            shown.add(n)
            out.append((n, f"{counts[n]} sessions (first: {t})"))
    return out


def render(sig, rows, total):
    out = [f"RETRO {n}: {t}" for n, t in collapse(sig)] or ["RETRO-NUMBERS: nothing tripped"]
    out.append(f"-- {len(rows)} transcript(s), total {total / 1000:.1f}k weighted units; top 8 by cost (kind, description, turns, peak context, units):")
    for kind, desc, r in sorted(rows, key=lambda x: -x[2]["units"])[:8]:
        out.append(f"   {kind:<13} {desc[:40]:<40} {r['turns']:>4} turns  {r['peak_ctx'] // 1000:>4}k ctx  {r['units'] / 1000:>7.1f}k")
    return "\n".join(out)


def _json_lines(path):
    """Every line of a JSONL file that parses as a JSON object, in order (other JSON values and garbage are skipped)."""
    return read_jsonl(path, quiet=True, dicts_only=True, errors="replace")


def _session_records(path):
    """The `session` records of metrics.jsonl."""
    return [r for r in _json_lines(path) if r.get("ev") == "session"]


def _wave_tools(ss):
    """-> (tools {name: [calls, est. cost]}, roles {agent: {name: calls}}, sessions without tool data, wave cost) for one wave's sessions."""
    tools: dict = {}
    roles: dict = {}
    notools, cost = 0, 0.0
    for r in ss:
        c = float(r.get("cost_usd") or 0)
        cost += c
        t = {k: int(v) for k, v in (r.get("tools") or {}).items() if isinstance(v, (int, float)) and v > 0}
        n = sum(t.values())
        if not n:
            notools += r.get("agent") != "script"  # a script step runs no model: no tool data by design
            continue
        rl = roles.setdefault(r.get("agent") or "?", {})
        for k, v in t.items():
            a = tools.setdefault(k, [0, 0.0])
            a[0] += v
            a[1] += c * v / n
            rl[k] = rl.get(k, 0) + v
    return tools, roles, notools, cost


def wave_breakdown(metrics_path):
    """Per-wave lines from metrics.jsonl `session` records: sessions, cost, tool calls with share and ESTIMATED cost
    (session cost split by that session's tool-call share), per-role calls. Sessions without tools count as "no tool data"."""
    if not metrics_path or not os.path.exists(metrics_path):
        return []
    waves: dict = {}
    for r in _session_records(metrics_path):
        waves.setdefault(r.get("wave"), []).append(r)
    out = []
    for w, ss in sorted(waves.items(), key=lambda kv: str(kv[0])):
        tools, roles, notools, cost = _wave_tools(ss)
        out.append(f"-- wave {w}: {len(ss)} session(s), ${cost:.2f}, {notools} with no tool data")
        total = sum(a[0] for a in tools.values())
        if total:
            out.append("   tool            calls  share  est. cost (estimated, not measured per tool)")
            for k, (n, c) in sorted(tools.items(), key=lambda kv: -kv[1][0])[:12]:
                out.append(f"   {k[:14]:<14} {n:>6} {n / total:>5.0%}  ${c:.2f}")
            for a, rl in sorted(roles.items()):
                out.append(f"   role {a}: " + ", ".join(f"{k} {v}" for k, v in sorted(rl.items(), key=lambda kv: -kv[1])[:8]))
    return out


def _no_tool_data(r):
    """True for a model session whose record has no positive tool-call count."""
    t = r.get("tools")
    return r.get("agent") != "script" and not (isinstance(t, dict) and any(isinstance(v, (int, float)) and v > 0 for v in t.values()))


def _wave_costs(sessions):
    """{wave: summed cost_usd} over model sessions that name a wave; script steps would drag the median to $0."""
    waves: dict = {}
    for r in sessions:
        if r.get("agent") != "script" and r.get("wave") is not None:
            waves[r.get("wave")] = waves.get(r.get("wave"), 0.0) + float(r.get("cost_usd") or 0)
    return waves


def _wave_cost_signals(waves):
    """wave-cost signals for waves costing more than WAVE_MULT x the median wave (and at least WAVE_FLOOR_USD); [] below WAVE_MIN waves."""
    if len(waves) < WAVE_MIN:
        return []
    med = statistics.median(waves.values())
    return [
        ("wave-cost", f"wave {w} costs ${c:.2f}, {c / med:.1f}x the median wave (${med:.2f})")
        for w, c in sorted(waves.items(), key=lambda kv: -kv[1])
        if med and c > WAVE_MULT * med and c >= WAVE_FLOOR_USD
    ]


def metrics_signals(metrics_path):
    """wave-cost and tools-missing signals from metrics.jsonl `session` records; [] when the file is missing."""
    if not metrics_path or not os.path.exists(metrics_path):
        return []
    sessions = _session_records(metrics_path)
    miss = sum(1 for r in sessions if _no_tool_data(r))
    sig = _wave_cost_signals(_wave_costs(sessions))
    n = sum(1 for r in sessions if r.get("agent") != "script")  # script steps run no model
    if n and miss / n > NOTOOLS_SHARE:
        sig.append(("tools-missing", f"{miss} of {n} sessions without tool data"))
    return sig + context_signals(metrics_path)


def _context_event_signals(r):
    sig = []
    if not r.get("code_files"):
        sig.append(("context-skipped", "no files named: local model down, index failed or nothing matched; the planner explored alone"))
    if (r.get("secs_total") or 0) > CTX_SLOW_S:
        sig.append(
            (
                "context-slow",
                f"context step took {r['secs_total']} s (limit {CTX_SLOW_S}; index {r.get('secs_index')} s, model {r.get('secs_model')} s)",
            )
        )
    ended = str(r.get("explore_ended") or "")
    if ended and ended != "answer":
        sig.append(
            (
                "context-explore-cut",
                f"explore loop ended by `{ended}` after {r.get('explore_turns')} turns, {r.get('explore_calls')} model calls (slowest {r.get('explore_slowest_call')} s): the file list is partial or the script fallback ran",
            )
        )
    return sig


def _context_recall_signals(r):
    if (r.get("total") or 0) >= CTX_RECALL_MIN_FILES and r["hits"] / r["total"] < CTX_RECALL_MIN:
        return [
            (
                "context-recall",
                f"only {r['hits']} of {r['total']} modified files were in plan-context.md; missed: {', '.join(r.get('missed') or [])[:200]}",
            )
        ]
    return []


def context_signals(metrics_path):
    """Planner-context signals from `context` and `context-recall` event lines of metrics.jsonl. A skipped step is reported as skipped, never as fine."""
    sig = []
    for r in _json_lines(metrics_path):
        if r.get("local_model") is False:  # model off for the run (preflight STRATA=0): no context expected, not a fault
            continue
        if r.get("ev") == "context":
            sig += _context_event_signals(r)
        elif r.get("ev") == "context-recall":
            sig += _context_recall_signals(r)
    return sig


def _script_ids(evs, why_prefix):
    """{dispatch id: task} of the script dispatches whose `why` starts with the prefix."""
    return {e["id"]: e["task"] for e in evs if e.get("ev") == "dispatch" and e.get("role") == "script" and str(e.get("why") or "").startswith(why_prefix)}


def _report_index(evs, i):
    return next((n for n, e in enumerate(evs) if e.get("ev") == "report" and e.get("id") == i), None)


def prec(r):
    """`, precision 72% of 39 named, mode explore` for a context-recall event that has the fields; "" for an older one."""
    return f", precision {r['precision']:.0%} of {r['named']} named, mode {r.get('mode') or '?'}" if r.get("named") else ""


def recall_lines(metrics_path):
    """One retro line per `context-recall` event, good or bad: how many of the modified files the planner context named (the trend, not only the alarm). [] when none."""
    out = []
    for r in _json_lines(metrics_path) if metrics_path and os.path.exists(metrics_path) else []:
        if r.get("ev") == "context-recall" and r.get("total"):
            out.append(
                f"plan-context recall: {r['hits']} of {r['total']} modified files named ({r['hits'] / r['total']:.0%}, goal >= {CTX_RECALL_MIN:.0%}){prec(r)}; missed: {', '.join(r.get('missed') or [])[:160] or 'none'}"
            )
    return out


def _phase_size_summary(evs):
    """The `phase-size summary` line: over/under/equal counts and mean actual/estimate over the rows with estimate > 0."""
    usable = [r for r in evs if (r.get("estimate") or 0) > 0]
    over = sum(1 for r in usable if r["actual"] > r["estimate"])
    under = sum(1 for r in usable if r["actual"] < r["estimate"])
    equal = len(usable) - over - under
    ratio = f"{statistics.mean(r['actual'] / r['estimate'] for r in usable):.2f}" if usable else "n/a"
    return f"phase-size summary: {len(evs)} phases, estimate vs actual: over {over}, under {under}, equal {equal} (mean actual/estimate {ratio})"


def phase_size_lines(metrics_path):
    """One retro line per `phase-size` event (estimate vs actual task count) plus a summary line. Ratio uses only estimate > 0. [] when none."""
    evs = [r for r in _json_lines(metrics_path) if r.get("ev") == "phase-size"] if metrics_path and os.path.exists(metrics_path) else []
    if not evs:
        return []
    out = [f"phase-size: {r.get('phase')} estimate {r.get('estimate')} actual {r.get('actual')}" for r in evs]
    out.append(_phase_size_summary(evs))
    return out


def resolved_lines(orch_path):
    """Retro lines for the `resolved` events (a merger resolved a conflict: `RESOLVED: path: both|dev|target: why`): the count, then each line, so the lead reviews them before the push. [] when none."""
    if not orch_path or not os.path.exists(orch_path):
        return []
    evs = [r for r in _json_lines(orch_path) if r.get("ev") == "resolved"]
    if not evs:
        return []
    return [
        f"merge conflicts resolved by the merger: {len(evs)} file(s), review before push",
        *(f"  {r.get('task', '?')}: {str(r.get('line', ''))[:200]}" for r in evs[:20]),
    ]


def _gate_outcome(evs, k, task):
    """-> counter key for one local pre-review report at index k: 'skipped', or '<fail|ok>_<pass|fail|open>' with the paid reviewer's verdict."""
    if evs[k].get("verdict") == "SKIPPED":  # the gate judged nothing: neither a pass nor a miss
        return "skipped"
    local = "fail" if evs[k].get("verdict") == "FAIL" else "ok"
    rv = [e.get("verdict") for e in evs[k + 1 :] if e.get("task") == task and e.get("ev") == "report" and e.get("role") == "reviewer"]
    return f"{local}_" + ("open" if not rv else "pass" if rv[0] == "PASS" else "fail")


def prereview_lines(orch_path):
    """One retro line on the local pre-review gate: what it caught and what the paid reviewer then said. [] when it never ran."""
    if not orch_path or not os.path.exists(orch_path):
        return []
    evs = _json_lines(orch_path)
    ids = _script_ids(evs, "local pre-review")
    if not ids:
        return []
    c = {"fail_pass": 0, "fail_fail": 0, "fail_open": 0, "ok_pass": 0, "ok_fail": 0, "ok_open": 0, "skipped": 0}  # nosec  # false positive: dict keys, not a password
    for i, task in ids.items():
        k = _report_index(evs, i)
        if k is not None:
            c[_gate_outcome(evs, k, task)] += 1
    return [
        (
            f"pre-review (local gate): {len(ids) - c['skipped']} gated; local FAIL {c['fail_pass'] + c['fail_fail'] + c['fail_open']} (paid reviewer then PASS {c['fail_pass']}, FAIL {c['fail_fail']}, not yet {c['fail_open']}); "
            f"local pass {c['ok_pass'] + c['ok_fail'] + c['ok_open']} (reviewer PASS {c['ok_pass']}, FAIL {c['ok_fail']} = missed by the gate, not yet {c['ok_open']}); "
            f"{c['skipped']} skipped: no diff / too big / error"
        )
    ]


BLOCK_CLASSES = (
    ("permission", ("approval", "denied", "permission", "not allowed", "sandbox")),
    ("precondition", ("missing", "not found", "no such", "does not exist", "depends", "before", "not merged", "precondition")),
)


def block_class(reason):
    """permission | precondition | defect, from the words of a BLOCKED report's reason."""
    low = (reason or "").lower()
    return next((name for name, words in BLOCK_CLASSES if any(w in low for w in words)), "defect")


def blocked_lines(orch_path):
    """One retro line: BLOCKED reports per cause class, so the next fix targets the biggest one. [] when there are none."""
    if not orch_path or not os.path.exists(orch_path):
        return []
    n = collections.Counter(block_class(e.get("reason")) for e in _json_lines(orch_path) if e.get("ev") == "report" and e.get("verdict") == "BLOCKED")
    if not n:
        return []
    return [f"BLOCKED reports {sum(n.values())}: " + ", ".join(f"{k} {n[k]}" for k in ("permission", "precondition", "defect"))]


def _count_review_round(rounds, e):
    """Counts reviewer report `e` as one round for its task, until that task's first PASS."""
    r = rounds.setdefault(e["task"], {"n": 0, "ok": False})
    if not r["ok"]:
        r["n"] += 1
        r["ok"] = e.get("verdict") == "PASS"


def _rounds_distribution(done):
    """`1: a, 2: b, 3+: c` for the round counts of the tasks that reached a PASS."""
    return ", ".join(f"{k}: {sum(1 for n in done if n == k)}" for k in (1, 2)) + f", 3+: {sum(1 for n in done if n >= 3)}"


def review_round_lines(orch_path):
    """Retro line: reviewer reports per task until the first PASS (rounds-to-pass) and how many tasks never passed. [] when no reviewer report."""
    if not orch_path or not os.path.exists(orch_path):
        return []
    rounds: dict = {}
    for e in _json_lines(orch_path):
        if e.get("ev") == "report" and e.get("role") == "reviewer" and e.get("task"):
            _count_review_round(rounds, e)
    if not rounds:
        return []
    done = [r["n"] for r in rounds.values() if r["ok"]]
    nopass = len(rounds) - len(done)
    return [f"review rounds to first PASS over {len(rounds)} task(s): {_rounds_distribution(done)}; never passed: {nopass}"]


def _has(after, ev, **kw):
    return any(e.get("ev") == ev and all(e.get(k) == v for k, v in kw.items()) for e in after)


def _gate_consequence(evs, k, task, veto):
    """-> the consequence label for one gate report at index k: veto -> retry|skip|open, go -> fixed|notfixed|open."""
    after = [e for e in evs[k + 1 :] if e.get("task") == task]
    passed = _has(after, "report", role="reviewer", verdict="PASS")
    if veto:
        first = next((e["ev"] for e in after if e.get("ev") in ("retry", "skip")), None)
        return "open" if first is None else f"{first}+pass" if passed else first
    sonnet = _has(after, "dispatch", role="dev", model=MODEL_SONNET)
    if passed:
        return "fixed" if sonnet else "open"
    return "notfixed" if sonnet and _has(after, "report", role="dev") else "open"


def _gate_rows(evs):
    """-> [(id, task, veto, skipped, consequence, first answer line)] for every escalation-gate report."""
    rows = []
    for i, task in _script_ids(evs, "escalation gate").items():
        k = _report_index(evs, i)
        if k is None:
            continue
        rep = evs[k]
        text = ""
        with contextlib.suppress(OSError):
            text = open(rep.get("file") or "", encoding="utf-8", errors="replace").read()  # noqa: SIM115
        veto = rep.get("verdict") == "FAIL"
        rows.append((i, task, veto, "gate skipped" in text, _gate_consequence(evs, k, task, veto), [*text.splitlines(), "", ""][1][:160]))
    return rows


def _veto_part(rows, n):
    """The `vetoes ...` clause of the gate summary; `n` counts all consequences."""
    vetoes = [r for r in rows if r[2]]
    return f"vetoes {len(vetoes)} (lead retry {n['retry']}, retry then PASS {n['retry+pass']}, skip {n['skip']}+{n['skip+pass']}, open {sum(1 for r in vetoes if r[4] == 'open')})"


def _go_part(rows):
    """The `go-on ...` clause of the gate summary: go-on rows that were not skipped, by what Sonnet did."""
    gos = [r for r in rows if not r[2] and not r[3]]
    return f"go-on {len(gos)} (Sonnet fixed {sum(1 for r in gos if r[4] == 'fixed')}, Sonnet did not fix {sum(1 for r in gos if r[4] == 'notfixed')}, open {sum(1 for r in gos if r[4] == 'open')})"


def _gate_summary(rows):
    """One line: gate calls, then the veto and go-on clauses and the rows with no answer."""
    n = collections.Counter(r[4] for r in rows)
    return f"gate (local judge before Sonnet): {len(rows)} calls; {_veto_part(rows, n)}; {_go_part(rows)}; no answer {sum(1 for r in rows if r[3])}"


def gate_lines(orch_path):
    """Retro lines on the escalation gate (local judge before Sonnet): calls, vetoes and what the lead did next, go-ons and whether Sonnet then fixed the task. [] when it never ran."""
    if not orch_path or not os.path.exists(orch_path):
        return []
    rows = _gate_rows(_json_lines(orch_path))
    if not rows:
        return []
    return [_gate_summary(rows), *[f"gate {'veto' if r[2] else 'go'} {r[1]} {r[0]}: {r[5]} -> {r[4]}" for r in rows if not r[3]]]


def _stage_zero(reason):
    """-> the stage's LEAD-COST line when it has no cost: $0.00 with the reason."""
    return f"LEAD-COST stage: $0.00, reason: {reason}"


def _stage_lead_lines(a):
    """LEAD-COST lines for the stage: leads (not the stage's headless sessions) inside the stage's time window, then the whole-session line.
    Never []: without cost one line says why (`no orch`, `no window`, `no lead turns`)."""
    if not a.orch:
        return [_stage_zero("no orch")]
    lo, hi = lead_cost.window([a.orch])
    if not hi:
        return [_stage_zero("no window")]
    paths = lead_cost.lead_paths(a.paths)
    lines = lead_cost.lead_lines(paths, lo, hi)
    if not lines:
        return [_stage_zero("no lead turns")]
    inside = [p for p in paths if lead_cost.analyse_lead(p, lo, hi)["turns"]]
    whole = lead_cost.whole_session(inside)
    tail = f"; unmatched {whole['unmatched']}" if whole["unmatched"] else ""
    total = whole["lead_usd"] + whole["agent_usd"]
    return [
        *lines,
        (
            f"LEAD-COST whole session: ${total:.2f} incl. {whole['agents']} subagent log(s) "
            f"(leads ${whole['lead_usd']:.2f} + subagents ${whole['agent_usd']:.2f}){tail}"
        ),
    ]


def digest_check(metrics_path, digest_path):
    """-> [("digest-stale", text)] when the sessions/USD in retro-digest.md differ from the `session` rows of metrics.jsonl (digest taken too early or without ops); [] when equal or a file is missing."""
    if not (metrics_path and digest_path and os.path.exists(metrics_path) and os.path.exists(digest_path)):
        return []
    with open(digest_path, encoding="utf-8", errors="replace") as fh:
        m = re.search(DIGEST_TOTAL_RE, fh.read())
    n, usd = session_totals(metrics_path)
    if m and (int(m.group(1)) != n or abs(float(m.group(2)) - usd) >= CENT):
        return [
            (
                "digest-stale",
                f"retro-digest.md has {m.group(1)} sessions ${m.group(2)}, metrics.jsonl has {n} sessions ${usd:.2f}; rerun `ops_steps.py digest` at retro time",
            )
        ]
    return []


def _stage_window(orch_path):
    """-> (lo, hi) time window of the stage from its orchestrator log; (0.0, 0.0) without one."""
    return lead_cost.window([orch_path]) if orch_path else (0.0, 0.0)


def _read_metrics(metrics_path):
    """-> the rows of metrics.jsonl; [] when the file is missing."""
    return _json_lines(metrics_path) if os.path.exists(metrics_path) else []


def _stage_of(rows):
    """-> the stage name of the first metrics row that has one, or ''."""
    return next((r.get("stage") for r in rows if r.get("stage")), "")


def lead_rows(metrics_path, orch_path, paths):
    """Append one `lead` event per lead session of the stage window to metrics.jsonl (once per sid); -> the rows written."""
    lo, hi = _stage_window(orch_path)
    if not (metrics_path and hi):
        return []
    old = _read_metrics(metrics_path)
    known = {r.get("sid") for r in old if r.get("ev") == LEAD_EV}
    stage = _stage_of(old)
    out = []
    for p in lead_cost.lead_paths(paths):
        sid, r = os.path.basename(p)[:-6], lead_cost.analyse_lead(p, lo, hi)
        if r["turns"] and sid not in known:
            out.append(
                {"ev": LEAD_EV, "ts": now(), "stage": stage, "sid": sid, "turns": r["turns"], "cost_usd": round(sum(r["usd"].values()), 4), "usd": r["usd"]}
            )
    for rec in out:
        append_jsonl(metrics_path, rec)
    return out


def _new_unmatched(old, count):
    """True when COUNT > 0 and metrics rows OLD do not already hold a `lead-agent-unmatched` event with that count."""
    return bool(count) and not any(r.get("ev") == LEAD_AGENT_UNMATCHED_EV and r.get("count") == count for r in old)


def lead_agent_events(metrics_path, orch_path, paths):
    """Append one `lead-agent` event per agent a lead spawned inside the stage window (once per lead sid and agent), and a `lead-agent-unmatched` count when some logs could not be tied to the window; -> the rows written."""
    lo, hi = _stage_window(orch_path)
    if not (metrics_path and hi):
        return []
    old = _read_metrics(metrics_path)
    known = {(r.get("sid"), r.get("agent")) for r in old if r.get("ev") == LEAD_AGENT_EV}
    stage = _stage_of(old)
    rows, unmatched = lead_cost.lead_agent_rows(paths, lo, hi)
    out = [{"ev": LEAD_AGENT_EV, "ts": now(), "stage": stage, **r} for r in rows if (r["sid"], r["agent"]) not in known]
    if _new_unmatched(old, unmatched):
        out.append({"ev": LEAD_AGENT_UNMATCHED_EV, "ts": now(), "stage": stage, "count": unmatched})
    for rec in out:
        append_jsonl(metrics_path, rec)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=[os.path.join(HOME, "projects")])
    ap.add_argument("--orch", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--metrics", default="", help="metrics.jsonl for the per-wave breakdown (default: next to --orch)")
    ap.add_argument("--verdict", action="store_true", help="print `VERDICT: DONE` first (script step reports)")
    a = ap.parse_args(argv)
    m = a.metrics or (os.path.join(os.path.dirname(a.orch), METRICS_LOG) if a.orch else "")
    sig, rows, total = analyse(a.paths, stage_start(a.orch), ledger.metrics_sids(m))
    lead_rows(m, a.orch, a.paths)
    lead_agent_events(m, a.orch, a.paths)
    digest = os.path.join(os.path.dirname(m), "retro", "retro-digest.md") if m else ""
    text = render(sig + metrics_signals(m) + digest_check(m, digest), rows, total)
    text = "\n".join(
        [
            text,
            *_stage_lead_lines(a),
            *wave_breakdown(m),
            *prereview_lines(a.orch),
            *review_round_lines(a.orch),
            *gate_lines(a.orch),
            *resolved_lines(a.orch),
            *blocked_lines(a.orch),
            *recall_lines(m),
            *phase_size_lines(m),
        ]
    )
    if a.verdict:
        print("VERDICT: DONE")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)  # the stage's retro/ dir may not exist yet
        with open(a.out, "w") as f:
            f.write(text + "\n")
        print(f"retro numbers written to {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
