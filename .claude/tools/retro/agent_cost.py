#!/usr/bin/env python3
"""Summarise the cost drivers of one agent transcript (JSONL).

    python3 -I /root/.claude/tools/retro/agent_cost.py <log.jsonl> [--top N]
    python3 -I /root/.claude/tools/retro/agent_cost.py --sid <session id> [--top N]

<log.jsonl> is a session transcript, or a pipeline metrics.jsonl (session lines with sid, model, agent, tokens,
cost_usd): the latter prints per model/agent aggregates. --sid finds the transcript under ~/.claude/projects.
Symlinks are followed by open(). Non-JSON lines and missing fields are skipped.
Sizes are characters; tokens are estimated as chars / 4. USD comes from the table in pipeline/prices.py
(unknown models: derived estimate, marked).
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import prices
from pipeline.util import parse_jsonl

from retro import ledger

LIMITER_RE = re.compile(r"\b(head|tail|grep)\b")
BIG_BASH_CHARS = 4000
TURN_LIMIT = 30


def load(path):
    """-> (the JSON objects of a transcript, number of lines that are not one: unreadable or another JSON value)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        records, bad = parse_jsonl(f)
    objects = [r for r in records if isinstance(r, dict)]
    return objects, len(bad) + len(records) - len(objects)


def result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def describe(name, inp):
    if not isinstance(inp, dict):
        return name or "?"
    if name == "Bash":
        cmd = str(inp.get("command", "")).replace("\n", " ")
        return cmd[:80]
    for key in ("file_path", "path", "pattern", "url", "query", "description"):
        if inp.get(key):
            return str(inp[key])[:80]
    return (name or "?")[:80]


def _count_usage(usage, totals):
    """Add one assistant turn's token usage to totals; -> its context size (input + cache read + cache creation)."""
    inp = int(usage.get("input_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    cc = int(usage.get("cache_creation_input_tokens") or 0)
    totals["input"] += inp
    totals["cache_read"] += cr
    totals["cache_creation"] += cc
    totals["output"] += int(usage.get("output_tokens") or 0)
    totals["turns"] += 1
    return inp + cr + cc


def _count_tool_uses(content, calls, tool_calls, read_counts, bash_counts, totals):
    """Record every tool_use block of one assistant turn; -> list of `Name desc` strings."""
    names: list = []
    if not isinstance(content, list):
        return names
    for blk in content:
        if not isinstance(blk, dict) or blk.get("type") != "tool_use":
            continue
        name = blk.get("name") or "?"
        inp_d = blk.get("input") or {}
        desc = describe(name, inp_d)
        calls[blk.get("id")] = (name, desc, inp_d)
        tool_calls[name] += 1
        totals["tool_calls"] += 1
        names.append(f"{name} {desc}")
        if name == "Read" and isinstance(inp_d, dict) and inp_d.get("file_path"):
            read_counts[str(inp_d["file_path"])] += 1
        if name == "Bash" and isinstance(inp_d, dict):
            bash_counts[str(inp_d.get("command", ""))] += 1
    return names


def _tool_results(content, calls):
    """One result dict per tool_result block of a user record."""
    results = []
    for blk in content:
        if not isinstance(blk, dict) or blk.get("type") != "tool_result":
            continue
        name, desc, inp_d = calls.get(blk.get("tool_use_id"), ("?", "?", {}))
        text = result_text(blk)
        cmd = inp_d.get("command", "") if isinstance(inp_d, dict) else ""
        results.append(
            {
                "tool": name,
                "desc": desc,
                "chars": len(text),
                "cmd": str(cmd) if name == "Bash" else None,
            }
        )
    return results


def analyse(records):
    turns = []  # one dict per assistant record
    calls: dict = {}  # tool_use id -> (name, desc, command_or_path)
    results = []  # dicts: tool, desc, chars, cmd
    tool_calls: Counter[str] = Counter()
    read_counts: Counter[str] = Counter()
    bash_counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    seen_ids = set()  # usage is repeated on every content-block record of one message

    for rec in records:
        kind = rec.get("type")
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if kind == "assistant":
            mid = msg.get("id")
            first = mid is None or mid not in seen_ids
            seen_ids.add(mid)
            ctx = _count_usage(msg.get("usage") or {}, totals) if first else None
            names = _count_tool_uses(content, calls, tool_calls, read_counts, bash_counts, totals)
            if first:
                turns.append(
                    {
                        "ctx": ctx,
                        "preceding": "; ".join(names) if names else "(text only)",
                    }
                )
        elif kind == "user" and isinstance(content, list):
            results += _tool_results(content, calls)

    return turns, tool_calls, results, read_counts, bash_counts, totals


def fmt_int(n):
    return f"{n:,}"


def _totals_section(totals):
    out = ["1. Totals"]
    out.append(f"   assistant turns {fmt_int(totals['turns'])}, tool calls {fmt_int(totals['tool_calls'])}")
    out.append(
        f"   output {fmt_int(totals['output'])}, cache_creation {fmt_int(totals['cache_creation'])}, "
        f"cache_read {fmt_int(totals['cache_read'])}, fresh input {fmt_int(totals['input'])}"
    )
    if totals["turns"] > TURN_LIMIT:
        out.append(f"   RETRO: {totals['turns']} turns > {TURN_LIMIT} (planner/dev budget)")
    return out


def _per_tool_section(tool_calls, results):
    out = ["2. Per tool (calls, result chars, ~tokens)"]
    by_tool: defaultdict[str, int] = defaultdict(int)
    for r in results:
        by_tool[r["tool"]] += r["chars"]
    names = set(tool_calls) | set(by_tool)
    for name in sorted(names, key=lambda n: -by_tool.get(n, 0)):
        chars = by_tool.get(name, 0)
        out.append(f"   {name:<14} calls {tool_calls.get(name, 0):>4}  chars {fmt_int(chars):>10}  ~tok {fmt_int(chars // 4):>9}")
    return out


def _repeats_section(read_counts, bash_counts):
    out = ["4. Repeats"]
    rep_reads = [(p, c) for p, c in read_counts.most_common() if c > 1]
    rep_bash = [(p, c) for p, c in bash_counts.most_common() if c > 1 and p]
    if not rep_reads and not rep_bash:
        out.append("   none")
    for p, c in rep_reads:
        out.append(f"   Read x{c}  {p[:100]}")
    for p, c in rep_bash:
        out.append(f"   Bash x{c}  {p[:100]}")
    return out


def _unlimited_section(results, top_n):
    out = [f"5. Bash results > {BIG_BASH_CHARS} chars with no head/tail/grep"]
    unlimited = [r for r in results if r["tool"] == "Bash" and r["chars"] > BIG_BASH_CHARS and not LIMITER_RE.search(r["cmd"] or "")]
    if not unlimited:
        out.append("   none")
    for r in sorted(unlimited, key=lambda r: -r["chars"])[:top_n]:
        out.append(f"   {r['desc'][:80]:<80} {fmt_int(r['chars'])}")
    return out


def _growth_section(turns):
    out = ["6. Top 5 turns by context growth (vs previous turn)"]
    growth = []
    for i in range(1, len(turns)):
        growth.append((turns[i]["ctx"] - turns[i - 1]["ctx"], i, turns[i - 1]["preceding"]))
    growth.sort(key=lambda g: -g[0])
    if not growth:
        out.append("   none")
    for delta, i, prev in growth[:5]:
        out.append(f"   turn {i + 1:>4} +{fmt_int(delta)} ctx {fmt_int(turns[i]['ctx'])}  after: {prev[:100]}")
    return out


def _usd(model, inp, out, cr, cw):
    """-> (usd, estimated?) for one token set; unknown model -> derived rate on input-equivalent tokens."""
    c = prices.cost(model, inp, out, cr, cw)
    if c is not None:
        return c, False
    weighted = inp + 1.25 * cw + 0.1 * cr + 5 * out
    return weighted * 5 * prices.DERIVED_USD_PER_UNIT / 1000.0, True


def _count(v):
    """A token count from a usage field; 0 for anything that is not a number."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def model_costs(records):
    """-> {model: [input, output, cache_read, cache_write]} over transcript assistant messages (one per message id)."""
    last = {}
    for n, rec in enumerate(records):
        msg = rec.get("message")
        if rec.get("type") == "assistant" and isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            last[msg.get("id") or f"anon{n}"] = msg
    out: defaultdict[Any, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for msg in last.values():
        u = msg["usage"]
        row = out[msg.get("model") or "?"]
        for i, key in enumerate(("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            row[i] += _count(u.get(key))
    return out


def transcript_cost(path):
    """-> {"input", "output", "cache_read", "cache_write", "usd", "estimate"} summed over the models of one transcript plus "bad_lines" (lines that are not json objects); unreadable file: zeros."""
    try:
        records, bad = load(path)
    except (OSError, ValueError):
        records, bad = [], 0
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "usd": 0.0, "estimate": False, "bad_lines": bad}
    for model, t in model_costs(records).items():
        usd, est = _usd(model, *t)
        tot["usd"] += usd
        tot["estimate"] = tot["estimate"] or est
        for key, v in zip(("input", "output", "cache_read", "cache_write"), t, strict=True):
            tot[key] += v
    return tot


def _model_section(records):
    out = [f"7. Cost by model (USD, price table of {prices.PRICES_DATE}; re-check it)"]
    rows = model_costs(records)
    if not rows:
        out.append("   none")
    for model, t in sorted(rows.items()):
        usd, est = _usd(model, *t)
        out.append(f"   {model:<28} ${usd:>8.2f}{'  ESTIMATE: no price row, derived rate' if est else ''}")
    return out


def is_metrics(records):
    return any(r.get("ev") == "session" for r in records)


def metrics_report(records, path):
    """Per (model, agent) aggregates over session lines: recorded cost_usd next to the price-table cost."""
    groups: defaultdict[tuple, dict[str, Any]] = defaultdict(lambda: {"n": 0, "turns": 0, "tok": [0, 0, 0, 0], "rec": 0.0})
    for r in records:
        if r.get("ev") != "session":
            continue
        g = groups[(r.get("model") or "?", r.get("agent") or "?")]
        g["n"] += 1
        g["turns"] += int(r.get("turns") or 0)
        g["rec"] += float(r.get("cost_usd") or 0)
        for i, k in enumerate(("input", "output", "cache_read", "cache_write")):
            g["tok"][i] += int(r.get(k) or 0)
    out = [f"agent_cost: {path} (metrics file: {sum(g['n'] for g in groups.values())} sessions)"]
    out.append(f"   {'model':<22} {'agent':<12} {'runs':>4} {'turns':>5} {'output':>9} {'cache_rd':>11} {'recorded':>9} {'table':>9}")
    est = set()
    for (model, agent), g in sorted(groups.items(), key=lambda kv: -kv[1]["rec"]):
        usd, e = _usd(model, *g["tok"])
        if e:
            est.add(model)
        out.append(
            f"   {model[:22]:<22} {agent[:12]:<12} {g['n']:>4} {g['turns']:>5} {fmt_int(g['tok'][1]):>9} {fmt_int(g['tok'][2]):>11} "
            f"${g['rec']:>8.2f} ${usd:>8.2f}{'*' if e else ''}"
        )
    out.append(f"   total recorded ${sum(g['rec'] for g in groups.values()):.2f}; table = price table of {prices.PRICES_DATE} (re-check it)")
    if est:
        out.append(f"   * ESTIMATE: no price row for {', '.join(sorted(est))}; derived rate from one Opus run, not a price")
    return "\n".join(out)


def find_transcript(sid):
    return ledger.find_transcript(sid)


def report(path, top_n):
    records, bad = load(path)
    if is_metrics(records):
        return metrics_report(records, path)
    turns, tool_calls, results, read_counts, bash_counts, totals = analyse(records)
    out = [f"agent_cost: {path}"]
    if bad:
        out.append(f"(skipped {bad} non-JSON lines)")
    out += _totals_section(totals)
    out += _per_tool_section(tool_calls, results)
    out.append(f"3. Top {top_n} largest tool results")
    for r in sorted(results, key=lambda r: -r["chars"])[:top_n]:
        out.append(f"   {r['tool']:<10} {r['desc'][:80]:<80} {fmt_int(r['chars'])}")
    out += _repeats_section(read_counts, bash_counts)
    out += _unlimited_section(results, top_n)
    out += _growth_section(turns)
    out += _model_section(records)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cost drivers of one agent transcript JSONL.")
    ap.add_argument("log", nargs="?")
    ap.add_argument("--sid", help="session id: find the transcript under ~/.claude/projects")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args(argv)
    path = args.log or find_transcript(args.sid)
    if not path:
        print("agent_cost: give a log path, or a --sid that exists under ~/.claude/projects", file=sys.stderr)
        return 2
    try:
        print(report(path, max(1, args.top)))
    except OSError as e:
        print(f"agent_cost: cannot read {path}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
