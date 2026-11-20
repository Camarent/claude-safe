#!/usr/bin/env python3
"""Feature retro input: gather the tripped `RETRO <signal>:` lines and the retro.md notes of every phase of a feature into one problems file.
Usage: feature_retro.py --feature-dir .work/F [--out FILE]. No model; the lead reads the file and finds causes. Signals seen in 2+ phases are listed first."""

import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline.configuration import HOME, ORCH_LOG, RETRO_ASK_CAUSE_CHARS
from pipeline.util import read_jsonl, work_path

from retro import agent_cost, lead_cost, ledger

SIGNAL_RE = re.compile(r"^RETRO ([\w-]+):\s*(.*)$")
ASK_TASK_RE = re.compile(r"^[\w.-]*\d[\w.-]*: ")  # "task-5: " or "merge-w1: " (an id with a digit) in front of the cause; "error: ..." is part of the cause
ASK_PATH_RE = re.compile(r"\S+\.md\b")  # a report file named in the ask


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def phase_dirs(feature_dir):
    root = os.path.join(feature_dir, "phases")
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))) if os.path.isdir(root) else []


def gather(feature_dir):
    """-> {phase: {"signals": [(name, text)], "notes": [lines of retro.md]}}"""
    out = {}
    for d in phase_dirs(feature_dir):
        base = os.path.join(feature_dir, "phases", d)
        sigs = [(m[1], m[2]) for line in read(work_path(base, "retro-numbers.md")).splitlines() if (m := SIGNAL_RE.match(line))]
        notes = [x for x in read(work_path(base, "retro.md")).splitlines() if x.strip()]
        asks = [e.get("message", "") for e in read_jsonl(os.path.join(base, ORCH_LOG)) if e.get("ev") == "ask"]
        out[d] = {"signals": sigs, "notes": notes, "asks": asks}
    return out


def ask_causes(data):
    """-> [(cause, count)] of the ask_main stops of all phases, most frequent first (task id, step and file paths removed, so the same cause in different tasks counts together)."""
    c = collections.Counter(ASK_PATH_RE.sub("<path>", ASK_TASK_RE.sub("", m)).split(";")[0][:RETRO_ASK_CAUSE_CHARS] for v in data.values() for m in v["asks"])
    return c.most_common()


def _ask_lines(data):
    """The `ask_main stops` section: the total, the count per phase and the causes."""
    total = sum(len(v["asks"]) for v in data.values())
    per_phase = ", ".join(f"{ph} {len(v['asks'])}" for ph, v in data.items())
    return ["", f"## ask_main stops ({total}, per phase: {per_phase})"] + ([f"- {n}x {cause}" for cause, n in ask_causes(data)] or ["(none)"])


def render(feature, data):
    names: dict = {}
    for ph, v in data.items():
        for n, _ in v["signals"]:
            names.setdefault(n, []).append(ph)
    lines = [f"# Feature problems: {feature}", "", f"{len(data)} phases. Facts only; causes are for the lead to find (hypothesis, not verified)."]
    rep = {n: p for n, p in names.items() if len(set(p)) > 1}
    lines += ["", "## Signals seen in 2+ phases"] + ([f"- {n}: {', '.join(sorted(set(p)))}" for n, p in sorted(rep.items())] or ["(none)"])
    lines += _ask_lines(data)
    for ph, v in data.items():
        lines += ["", f"## {ph}"]
        lines += [f"- {n}: {t[:300]}" for n, t in v["signals"]] or ["- no tripped signal"]
        if v["notes"]:
            lines += ["retro.md:"] + [f"  {x[:300]}" for x in v["notes"][:15]]
    return "\n".join(lines) + "\n"


def _cost_line(label, c):
    toks = c["input"] + c["output"] + c["cache_read"] + c["cache_write"]
    return f"- {label}: {toks} tokens (in {c['input']}, out {c['output']}, cache read {c['cache_read']}, cache write {c['cache_write']}), ${c['usd']:.2f}" + (
        " ESTIMATE: no price row" if c["estimate"] else ""
    )


def cost_lines(paths):
    """Cost lines for lead transcripts (files or project folders): `lead-session` per lead log, `lead-spawned` per agent log under its subagents/. Estimates from the token counts and pipeline/prices.py."""
    out = []
    for sid, lead, agents in ledger.find_sessions(paths):
        if lead:
            out.append(_cost_line(f"lead-session {sid}", agent_cost.transcript_cost(lead)))
        for log, meta in agents:
            who = meta.get("agentType") or meta.get("description") or os.path.basename(log)[:-6]
            out.append(_cost_line(f"lead-spawned {who} ({os.path.basename(log)[:-6]})", agent_cost.transcript_cost(log)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-dir", required=True)
    ap.add_argument("--out")
    ap.add_argument(
        "--lead-log", action="append", default=[], help="lead transcript file or folder (repeatable): adds lead-session and lead-spawned cost lines"
    )
    a = ap.parse_args(argv)
    text = render(os.path.basename(os.path.normpath(a.feature_dir)), gather(a.feature_dir))
    lead = lead_cost.feature_lines(a.feature_dir, a.lead_log or [os.path.join(HOME, "projects")])
    if lead:
        text += "\n## Lead cost of the whole feature (script, USD from prices.py)\n" + "\n".join(lead) + "\n"
    lines = cost_lines(a.lead_log) if a.lead_log else []
    if lines:
        text += "\n## Lead cost (estimate)\n" + "\n".join(lines) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"VERDICT: DONE\nfeature retro input written: {a.out}")  # the driver reads the step's output for its verdict; a silent run counted as FAIL
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
