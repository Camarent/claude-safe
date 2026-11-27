#!/usr/bin/env python3
"""Validate an efficiency audit file (retro-m25 / m26-fixes / docs-cleanup).

    python3 -I .claude/tools/tests/validate_efficiency_audit.py FILE
    python3 -I .claude/tools/tests/validate_efficiency_audit.py --selftest

Before: a table of exactly five sink rows; each row has one cell per run, a number or `not available`.
Fixes: exactly two bullets, each naming a repo path with `:LINE` that exists and a saving with a number.
Exit 0 when valid, 1 when not, 2 on usage error. Stdlib only (run with -I)."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
RUNS = ("retro-m25", "m26-fixes", "docs-cleanup")
SINKS = ("repeat reads", "re-planning", "review rounds", "Opus calls without a stated reason", "other")
NOT_AVAILABLE = "not available"
FIX_CITE_RE = re.compile(r"(\.claude/[\w./-]+\.\w+):(\d+)")
SECTION_RE = re.compile(r"^## (\w+)\b", re.MULTILINE)
REQUIRED_FIXES = 2


def _sections(text):
    """-> {name: body lines} for every `## Name` section."""
    heads = list(SECTION_RE.finditer(text))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[m.group(1)] = text[m.end() : end].splitlines()
    return out


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _before_problems(lines):
    rows = [ln for ln in lines if ln.strip().startswith("|") and not re.fullmatch(r"[\s|:-]+", ln.strip())]
    if not rows:
        return ["Before: no table"]
    header, body = _cells(rows[0]), rows[1:]
    probs = []
    if tuple(header[1:]) != RUNS:
        probs.append(f"Before: header must name the runs {', '.join(RUNS)}")
    if len(body) != len(SINKS):
        probs.append(f"Before: need exactly {len(SINKS)} sink rows, got {len(body)}")
    seen = set()
    for ln in body:
        cells = _cells(ln)
        sink = next((s for s in SINKS if cells[0].lower().startswith(s.lower())), None)
        if sink is None:
            probs.append(f"Before: row is not one of the sink kinds: {cells[0]!r}")
        elif sink in seen:
            probs.append(f"Before: sink {sink!r} listed twice")
        else:
            seen.add(sink)
        if len(cells) != len(RUNS) + 1:
            probs.append(f"Before: row {cells[0]!r} needs one cell per run ({len(RUNS)}), got {len(cells) - 1}")
            continue
        for run, cell in zip(RUNS, cells[1:]):
            if cell != NOT_AVAILABLE and not re.search(r"\d", cell):
                probs.append(f"Before: row {cells[0]!r} has no number for {run}: {cell!r}")
    return probs


def _fix_problems(lines, root):
    bullets = [ln for ln in lines if ln.startswith("- ")]
    probs = []
    if len(bullets) != REQUIRED_FIXES:
        probs.append(f"Fixes: need exactly {REQUIRED_FIXES} fixes, got {len(bullets)}")
    for ln in bullets:
        m = FIX_CITE_RE.search(ln)
        if not m:
            probs.append(f"Fixes: no repo path:line cite in {ln[:60]!r}")
            continue
        path = os.path.join(root, m.group(1))
        if not os.path.isfile(path):
            probs.append(f"Fixes: cited file does not exist: {m.group(1)}")
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            count = sum(1 for _ in fh)
        if int(m.group(2)) < 1 or int(m.group(2)) > count:
            probs.append(f"Fixes: line {m.group(2)} outside {m.group(1)} ({count} lines)")
        if not re.search(r"saving\D*\d", ln, re.IGNORECASE):
            probs.append(f"Fixes: no saving with a number in {ln[:60]!r}")
    return probs


def validate(text, root=ROOT):
    """-> list of problems; empty when the audit is valid."""
    secs = _sections(text)
    probs = []
    for name in ("Before", "Fixes"):
        if name not in secs:
            probs.append(f"missing section: ## {name}")
    if "Before" in secs:
        probs += _before_problems(secs["Before"])
    if "Fixes" in secs:
        probs += _fix_problems(secs["Fixes"], root)
    return probs


GOOD = """## Before

| sink | retro-m25 | m26-fixes | docs-cleanup |
|---|---|---|---|
| repeat reads | 0 | not available | not available |
| re-planning | 15 | not available | 2 |
| review rounds | 125 | not available | 5 |
| Opus calls without a stated reason | 0 | not available | 0 |
| other | 18 | not available | 3 |

## Fixes

- review rounds: .claude/tools/pipeline/plan_check_task.py:43 runs brieflint at plan time. Saving: about 0.008 USD per avoided reviewer session.
- other (retries): .claude/tools/pipeline/plan_check_task.py:41 imports brieflint. Saving: about 0.009 USD per avoided dev retry.
"""


def _selftest():
    """Decoys: a good file passes; four rows, a missing section, a missing number and a missing cited file each fail."""
    cases = [
        ("good file passes", GOOD, False),
        ("four sink rows fail", GOOD.replace("| other | 18 | not available | 3 |\n", ""), True),
        ("missing Fixes section fails", GOOD.split("## Fixes")[0], True),
        ("missing number fails", GOOD.replace("| re-planning | 15 |", "| re-planning | - |"), True),
        ("cite to missing file fails", GOOD.replace("plan_check_task.py:43", "no_such_file.py:1"), True),
    ]
    bad = []
    for label, text, want_problems in cases:
        got = bool(validate(text))
        if got != want_problems:
            bad.append(label)
    for label in bad:
        print(f"selftest FAIL: {label}")
    if not bad:
        print(f"selftest OK: {len(cases)} cases")
    return 1 if bad else 0


def main(argv):
    if len(argv) == 2 and argv[1] == "--selftest":
        return _selftest()
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        probs = validate(fh.read())
    for p in probs:
        print(p)
    if not probs:
        print("OK")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
