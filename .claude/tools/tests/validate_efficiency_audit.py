#!/usr/bin/env python3
"""Validate an efficiency audit file (retro-m25 / m26-fixes / docs-cleanup).

    python3 -I .claude/tools/tests/validate_efficiency_audit.py FILE
    python3 -I .claude/tools/tests/validate_efficiency_audit.py --after FILE
    python3 -I .claude/tools/tests/validate_efficiency_audit.py --selftest

Before: a table of exactly five sink rows; each row has one cell per run, a number or `not available`.
Fixes: exactly two bullets, each naming a repo path with `:LINE` that exists and a saving with a number.
--after also needs an After section (same table form, a cell marked measured or expected) and a `done:` entry per fix.
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
FIXED_SINKS = ("review rounds", "other")
DONE_RE = re.compile(r'\bdone:\s*grep\s+(?:-\w+\s+)?"([^"]+)"\s+(\S+)')


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


def _table_problems(lines, name="Before"):
    rows = [ln for ln in lines if ln.strip().startswith("|") and not re.fullmatch(r"[\s|:-]+", ln.strip())]
    if not rows:
        return [f"{name}: no table"]
    header, body = _cells(rows[0]), rows[1:]
    probs = []
    if tuple(header[1:]) != RUNS:
        probs.append(f"{name}: header must name the runs {', '.join(RUNS)}")
    if len(body) != len(SINKS):
        probs.append(f"{name}: need exactly {len(SINKS)} sink rows, got {len(body)}")
    seen = set()
    for ln in body:
        cells = _cells(ln)
        sink = next((s for s in SINKS if cells[0].lower().startswith(s.lower())), None)
        if sink is None:
            probs.append(f"{name}: row is not one of the sink kinds: {cells[0]!r}")
        elif sink in seen:
            probs.append(f"{name}: sink {sink!r} listed twice")
        else:
            seen.add(sink)
        if len(cells) != len(RUNS) + 1:
            probs.append(f"{name}: row {cells[0]!r} needs one cell per run ({len(RUNS)}), got {len(cells) - 1}")
            continue
        for run, cell in zip(RUNS, cells[1:]):
            if cell != NOT_AVAILABLE and not re.search(r"\d", cell):
                probs.append(f"{name}: row {cells[0]!r} has no number for {run}: {cell!r}")
    return probs


def _after_problems(secs, root=ROOT):
    """After: same table as Before; each fixed sink cell says measured or expected; each fix row has a `done:` entry."""
    if "After" not in secs:
        return ["missing section: ## After"]
    probs = _table_problems(secs["After"], "After")
    rows = {_cells(ln)[0].lower(): _cells(ln) for ln in secs["After"] if ln.strip().startswith("|")}
    for sink in FIXED_SINKS:
        cells = next((c for k, c in rows.items() if k.startswith(sink)), None)
        if cells is None:
            continue  # the table check above already reports the missing row
        for run, cell in zip(RUNS, cells[1:]):
            if cell != NOT_AVAILABLE and not (re.search(r"\d", cell) and re.search(r"\b(measured|expected)\b", cell)):
                probs.append(f"After: row {sink!r} cell {run} needs a number and the word measured or expected: {cell!r}")
    for ln in (x for x in secs.get("Fixes", []) if x.startswith("- ")):
        m = DONE_RE.search(ln)
        if not m:
            probs.append(f"Fixes: no 'done:' entry in {ln[:60]!r}")
            continue
        path = os.path.join(root, m.group(2))
        if not os.path.isfile(path):
            probs.append(f"Fixes: done: file does not exist: {m.group(2)}")
        else:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if m.group(1) not in fh.read():
                    probs.append(f"Fixes: done: text {m.group(1)!r} not found in {m.group(2)}")
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


def validate(text, root=ROOT, after=False):
    """-> list of problems; empty when the audit is valid (after=True also checks the After section and `done:` entries)."""
    secs = _sections(text)
    probs = []
    for name in ("Before", "Fixes"):
        if name not in secs:
            probs.append(f"missing section: ## {name}")
    if "Before" in secs:
        probs += _table_problems(secs["Before"])
    if "Fixes" in secs:
        probs += _fix_problems(secs["Fixes"], root)
    if after:
        probs += _after_problems(secs, root)
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


AFTER = """
## After

| sink | retro-m25 | m26-fixes | docs-cleanup |
|---|---|---|---|
| repeat reads | 0 expected | not available | not available |
| re-planning | 15 | not available | 2 |
| review rounds | 100 expected | not available | 4 expected |
| Opus calls without a stated reason | 0 | not available | 0 |
| other | 15 expected | not available | 2 expected |
"""
DONE_OK = ' done: grep -n "brieflint" .claude/tools/pipeline/plan_check_task.py'


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
    good_after = GOOD + AFTER
    done_after = good_after.replace("session.", "session." + DONE_OK).replace("retry.", "retry." + DONE_OK)
    after_cases = [
        ("after: good file passes", done_after, False),
        ("after: missing After section fails", GOOD, True),
        ("after: missing done entry fails", good_after, True),
        ("after: done grep matching nothing fails", done_after.replace('"brieflint"', '"no such text xyz"'), True),
        ("after: review rounds cell without marker fails", done_after.replace("| 4 expected |", "| 4 |"), True),
        ("after: other cell without marker fails", done_after.replace("| 15 expected |", "| 15 |"), True),
    ]
    for label, text, want_problems in after_cases:
        if bool(validate(text, after=True)) != want_problems:
            bad.append(label)
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
    after = len(argv) == 3 and argv[1] == "--after"
    if not after and (len(argv) != 2 or argv[1].startswith("--")):
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[-1], encoding="utf-8") as fh:
        probs = validate(fh.read(), after=after)
    for p in probs:
        print(p)
    if not probs:
        print("OK")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
