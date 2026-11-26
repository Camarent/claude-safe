#!/usr/bin/env python3
"""Cheap A/B of the inline caveman block in subagent prompts, measured mainly by output size.

    python3 -I ab_caveman.py plan --budget 8 [--role R] [--turns R=N] [--model R=M]
    python3 -I ab_caveman.py run --budget 8 --out DIR [same flags]
    python3 -I ab_caveman.py report DIR

Arm A = the agent file as it is (block inline). Arm B = the same body with the block between
<!-- caveman:begin/end --> removed. Both arms run the same prompt, in a fresh temp cwd, through the same
`claude -p` launcher the pipeline uses (pipeline/claude.py). The agent body goes in via `--agents` JSON, so no
agents dir is copied and the repo is never written. `run` stops as soon as the summed reported usd reaches the
budget; each session also gets `--max-budget-usd` for what is left. Each run appends one ev=session line to DIR/results.jsonl.
`plan` calls nothing: it prints the runs and the estimated cost (ROLES est_usd, from the cost table in
docs/design of the caveman A/B, section 1b).
"""

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, verdicts
from pipeline.configuration import AGENTS_DIR
from pipeline.util import append_jsonl, read_jsonl

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ARMS = ("A", "B")
B_MARK, E_MARK = "<!-- caveman:begin -->", "<!-- caveman:end -->"  # same markers as tools/sync_caveman.py:15
BLOCK_RE = re.compile(re.escape(B_MARK) + r".*?" + re.escape(E_MARK) + r"\n?", re.S)

# Roles: agent file, model, turn cap, tools, est_usd per session (cost table: caveman A/B design, section 1b; Haiku/Sonnet rows).
ROLES = {
    "reviewer": {"model": "haiku", "turns": 3, "tools": ["Read", "Grep", "Glob"], "est_usd": 0.012, "verdict": True},
    "scout": {"model": "haiku", "turns": 6, "tools": ["Read", "Grep", "Glob"], "est_usd": 0.02, "verdict": False},
    "arch-auditor": {"model": "sonnet", "turns": 4, "tools": ["Read", "Grep", "Glob"], "est_usd": 0.13, "verdict": False},
    "architect": {"model": "sonnet", "turns": 6, "tools": ["Read", "Write"], "est_usd": 0.24, "verdict": False},
    "dev": {"model": "haiku", "turns": 6, "tools": ["Read", "Write"], "est_usd": 0.023, "verdict": False},
}
DIFF_GOOD = "--- a/clamp.py\n+++ b/clamp.py\n@@ -1,2 +1,2 @@\n def clamp(x, lo, hi):\n-    return x\n+    return min(max(x, lo), hi)\n"
DIFF_BAD = "--- a/clamp.py\n+++ b/clamp.py\n@@ -1,2 +1,2 @@\n def clamp(x, lo, hi):\n-    return x\n+    return max(x, lo)\n"
SPEC = "Spec: add clamp(x, lo, hi) returning x limited to the closed range [lo, hi]."
# Prompts: read-only or write-only-in-temp-cwd; expect = verdict fixed by the fixture (reviewer only).
PROMPTS = {
    "reviewer": [
        {"id": "r1", "expect": "PASS", "text": f"{SPEC}\nReview the diff. First line `VERDICT: PASS` or `VERDICT: FAIL`, then reasons.\n{DIFF_GOOD}"},
        {"id": "r2", "expect": "FAIL", "text": f"{SPEC}\nReview the diff. First line `VERDICT: PASS` or `VERDICT: FAIL`, then reasons.\n{DIFF_BAD}"},
        {"id": "r3", "expect": "PASS", "text": f"{SPEC}\nReview the diff. First line `VERDICT: PASS` or `VERDICT: FAIL`, then reasons.\n{DIFF_GOOD}"},
    ],
    "scout": [
        {"id": "s1", "text": "Where is run_claude defined in this repo? Answer with path:line, one line."},
        {"id": "s2", "text": "Where is the complexity gate (ccn) defined in this repo? Answer with path:line, one line."},
        {"id": "s3", "text": "Where is verdict() defined in this repo? Answer with path:line, one line."},
    ],
    "arch-auditor": [
        {"id": "a1", "text": "Audit .claude/tools/sync_caveman.py briefly: at most 3 findings, each one line with path:line."},
        {"id": "a2", "text": "Audit .claude/tools/pipeline/metrics.py briefly: at most 3 findings, each one line with path:line."},
        {"id": "a3", "text": "Audit .claude/tools/pipeline/verdicts.py briefly: at most 3 findings, each one line with path:line."},
    ],
    "architect": [
        {
            "id": "p1",
            "text": "Goal: add a --verbose flag to a tiny CLI hello.py. Write TASKS.md in the current directory with 2 or 3 tasks, each with files and done criteria. Write only that file.",
        },
        {
            "id": "p2",
            "text": "Goal: add a --count N option to a tiny CLI counter.py. Write TASKS.md in the current directory with 2 or 3 tasks, each with files and done criteria. Write only that file.",
        },
        {
            "id": "p3",
            "text": "Goal: add a --upper flag to a tiny CLI echo.py. Write TASKS.md in the current directory with 2 or 3 tasks, each with files and done criteria. Write only that file.",
        },
    ],
    "dev": [
        {
            "id": "d1",
            "text": "Trivial task: in the current directory create greet.py with greet(name) returning 'hello, NAME'. Then write report.md: first line `VERDICT: PASS`, then a 3-line summary.",
        },
        {
            "id": "d2",
            "text": "Trivial task: in the current directory create add.py with add(a, b) returning the sum. Then write report.md: first line `VERDICT: PASS`, then a 3-line summary.",
        },
        {
            "id": "d3",
            "text": "Trivial task: in the current directory create title.py with title(s) returning s capitalised per word. Then write report.md: first line `VERDICT: PASS`, then a 3-line summary.",
        },
    ],
}


def split_frontmatter(text):
    """-> (fields dict of `key: value` lines, body). Agent files use one flat frontmatter block; no YAML library is installed."""
    if not text.startswith("---\n"):
        return {}, text
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        return {}, text
    fields = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields, body


def strip_block(body):
    """Body without its caveman block. Exactly one block is required: a missing or doubled block is an error, not a silent no-op."""
    if len(BLOCK_RE.findall(body)) != 1:
        raise ValueError(f"expected exactly one {B_MARK} block, found {len(BLOCK_RE.findall(body))}")
    return BLOCK_RE.sub("", body, count=1).strip() + "\n"


def arm_prompt(agents_dir, role, arm):
    """-> (description, system prompt) of ROLE for ARM. Arm A keeps the body as is."""
    fields, body = split_frontmatter(read_text(os.path.join(agents_dir, role + ".md")))
    if arm == "B":
        body = strip_block(body)
    elif B_MARK not in body:
        raise ValueError(f"{role}: arm A expects the inline caveman block")
    return fields.get("description") or role, body.strip()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def plan_runs(roles, turns, models, prompts=PROMPTS):
    """Interleaved run list: prompt index outer, role next; arm order alternates so cache and time drift cancel."""
    runs: list[dict] = []
    for k in range(max(len(prompts[r]) for r in roles)):
        for i, role in enumerate(roles):
            if k >= len(prompts[role]):
                continue
            p = prompts[role][k]
            order = ARMS if (k + i) % 2 == 0 else ARMS[::-1]
            for arm in order:
                runs.append(
                    {
                        "n": len(runs) + 1,
                        "role": role,
                        "model": models.get(role, ROLES[role]["model"]),
                        "turns": turns.get(role, ROLES[role]["turns"]),
                        "tools": ROLES[role]["tools"],
                        "prompt_id": p["id"],
                        "prompt": p["text"],
                        "expect": p.get("expect", ""),
                        "arm": arm,
                        "pair": f"{role}:{p['id']}",
                        "est_usd": ROLES[role]["est_usd"],
                    }
                )
    return runs


def run_args(run, agents_dir, description, system, budget_left):
    """Args for `claude -p` for one run: the same flags as a pipeline session plus the inline agent and the spend cap."""
    agent = {run["role"]: {"description": description, "prompt": system, "tools": run["tools"], "model": run["model"]}}
    return [
        "-p",
        "--agent",
        run["role"],
        "--agents",
        json.dumps(agent),
        "--model",
        run["model"],
        "--max-turns",
        str(run["turns"]),
        "--max-budget-usd",
        f"{budget_left:.4f}",
        "--output-format",
        "json",
        *claude.flags(),
        "--tools",
        ",".join(run["tools"]),
        "--allowedTools",
        ",".join(run["tools"]),
        "--add-dir",
        REPO,
        "--",
        run["prompt"],
    ]


def record(run, u):
    """One ev=session line: the metrics.jsonl shape (role, model, output, usd, turns, seconds) plus arm and pair."""
    text = u.get("result", "")
    verdict = verdicts.verdict(run["role"], text)[0] if ROLES[run["role"]]["verdict"] else ""
    return {
        "ev": "session",
        "n": run["n"],
        "role": run["role"],
        "model": run["model"],
        "arm": run["arm"],
        "pair": run["pair"],
        "prompt": run["prompt_id"],
        "output": u["output"],
        "words": len(text.split()),
        "usd": u["cost_usd"],
        "turns": u["turns"],
        "seconds": u["seconds"],
        "verdict": verdict,
        "expect": run["expect"],
        "agree": (verdict == run["expect"]) if run["expect"] else None,
        "error": u["error"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def execute(run, agents_dir, budget_left):
    """Run one session in a temp cwd (removed afterwards) -> (ev=session record, output text)."""
    description, system = arm_prompt(agents_dir, run["role"], run["arm"])
    args = run_args(run, agents_dir, description, system, budget_left)
    with tempfile.TemporaryDirectory(prefix="ab-caveman-") as cwd:
        res = claude.run_claude(args, cwd, claude.default_timeout())
    u = claude.usage(res)
    return record(run, u), u.get("result", "")


def progress(done, total, t0, spent, budget):
    """Flushed progress line on stderr after every run: done/total, elapsed, estimate of time left, spend."""
    el = time.time() - t0
    left = el / done * (total - done) if done else 0.0
    print(f"ab: {done}/{total} runs, {el:.0f}s elapsed, ~{left:.0f}s left, ${spent:.3f} of ${budget:.2f}", file=sys.stderr, flush=True)


def cmd_run(runs, budget, out, agents_dir):
    """Execute RUNS in order until the summed usd reaches BUDGET. Partial results stay in OUT/results.jsonl."""
    if os.path.exists(os.path.join(out, "results.jsonl")):
        raise SystemExit(f"refusing to append to existing {out}/results.jsonl: pick a new --out")
    results = os.path.join(out, "results.jsonl")
    spent, t0 = 0.0, time.time()
    for done, run in enumerate(runs):
        if spent >= budget:
            print(f"ab: budget ${budget:.2f} reached after {done} runs; stopping, partial results in {results}", file=sys.stderr, flush=True)
            break
        rec, text = execute(run, agents_dir, budget - spent)
        append_jsonl(results, rec)
        os.makedirs(os.path.join(out, "outputs"), exist_ok=True)
        with open(os.path.join(out, "outputs", f"{run['n']:03d}-{run['role']}-{run['prompt_id']}-{run['arm']}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        spent += rec["usd"]
        progress(done + 1, len(runs), t0, spent, budget)
    return spent


def median_or_none(xs):
    return statistics.median(xs) if xs else None


def summarise(rows):
    """Per role and arm: medians of output, words, turns, seconds; sum usd; verdict agreement (reviewer)."""
    out = {}
    for role in ROLES:
        for arm in ARMS:
            rs = [r for r in rows if r["role"] == role and r["arm"] == arm]
            if not rs:
                continue
            agreed = [r["agree"] for r in rs if r["agree"] is not None]
            out[(role, arm)] = {
                "n": len(rs),
                "output": median_or_none([r["output"] for r in rs]),
                "words": median_or_none([r["words"] for r in rs]),
                "usd": sum(r["usd"] for r in rs),
                "turns": median_or_none([r["turns"] for r in rs]),
                "agree": f"{sum(agreed)}/{len(agreed)}" if agreed else "-",
            }
    return out


def paired_diffs(rows):
    """Per role: median of (A output - B output) over prompts run in both arms, and its share of A."""
    by_pair: dict[tuple, dict] = {}
    for r in rows:
        by_pair.setdefault((r["role"], r["pair"]), {})[r["arm"]] = r["output"]
    res: dict[str, list] = {}
    for (role, _pair), arms in by_pair.items():
        if set(arms) == set(ARMS):
            res.setdefault(role, []).append((arms["A"] - arms["B"], arms["A"]))
    return {role: (median_or_none([d for d, _ in v]), median_or_none([100.0 * d / a for d, a in v if a])) for role, v in res.items()}


def fmt(x, nd=0):
    return "-" if x is None else f"{x:.{nd}f}"


def report_text(rows):
    lines = [f"{'role':<13}{'arm':<4}{'n':>3}{'out tok':>9}{'words':>8}{'turns':>7}{'usd':>9}  verdict agree"]
    for (role, arm), s in summarise(rows).items():
        lines.append(f"{role:<13}{arm:<4}{s['n']:>3}{fmt(s['output']):>9}{fmt(s['words']):>8}{fmt(s['turns'], 1):>7}{s['usd']:>9.4f}  {s['agree']}")
    lines.append("paired (A minus B, median over prompts): role, out tok diff, % of A")
    for role, (d, pct) in paired_diffs(rows).items():
        lines.append(f"  {role:<13}{fmt(d, 1):>9}  {fmt(pct, 1)}%")
    lines.append(f"total usd {sum(r['usd'] for r in rows):.4f} over {len(rows)} runs")
    return "\n".join(lines)


def parse_map(items, cast):
    """`ROLE=VALUE` flags -> dict; unknown roles are an error."""
    out = {}
    for it in items or []:
        role, eq, val = it.partition("=")
        if not eq or role not in ROLES:
            raise SystemExit(f"bad {it!r}: want ROLE=VALUE with ROLE in {', '.join(ROLES)}")
        out[role] = cast(val)
    return out


def build_runs(args):
    roles = args.role or list(ROLES)
    turns, models = parse_map(args.turns, int), parse_map(args.model, str)
    return plan_runs(roles, turns, models)


def cmd_plan(args):
    runs = build_runs(args)
    print(f"{'n':>3}  {'role':<13}{'model':<8}{'turns':>5}  {'arm':<3}{'prompt':<7}est usd")
    for r in runs:
        print(f"{r['n']:>3}  {r['role']:<13}{r['model']:<8}{r['turns']:>5}  {r['arm']:<3}{r['prompt_id']:<7}{r['est_usd']:.3f}")
    est = sum(r["est_usd"] for r in runs)
    print(f"runs {len(runs)}, estimated usd {est:.2f}, budget ${args.budget:.2f} ({'within' if est <= args.budget else 'OVER'} budget); nothing was called")


def cmd_run_args(args):
    runs = build_runs(args)
    os.makedirs(args.out, exist_ok=True)
    spent = cmd_run(runs, args.budget, args.out, args.agents_dir)
    print(f"ab: done, spent ${spent:.4f}; report: python3 -I {__file__} report {args.out}")


def cmd_report(args):
    print(report_text(read_jsonl(os.path.join(args.dir, "results.jsonl"), dicts_only=True)))


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        s = sub.add_parser(name)
        s.add_argument("--budget", type=float, required=True, help="hard spend cap in usd (summed reported usd)")
        s.add_argument("--role", action="append", choices=list(ROLES), help="limit to a role (repeatable)")
        s.add_argument("--turns", action="append", metavar="ROLE=N", help="turn cap override")
        s.add_argument("--model", action="append", metavar="ROLE=MODEL", help="model override")
        s.add_argument("--agents-dir", default=AGENTS_DIR, help="agent files to read (default: the pipeline agents dir)")
        if name == "run":
            s.add_argument("--out", required=True, help="new output dir (results.jsonl, outputs/)")
    r = sub.add_parser("report")
    r.add_argument("dir")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "plan":
        cmd_plan(args)
    elif args.cmd == "run":
        cmd_run_args(args)
    else:
        cmd_report(args)


if __name__ == "__main__":
    main()
