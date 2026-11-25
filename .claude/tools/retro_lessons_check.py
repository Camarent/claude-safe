"""Check a retro lessons report: the two sections, their rows, and the totals footer.

    python3 -I .claude/tools/retro_lessons_check.py docs/retro-lessons.md

A script, no model. Exit 1 and one `FAIL:` line per problem when the report has no `## Stuck causes` or
`## Useless escalations` section, a row whose report file does not exist (or that is not four cells), or a
`Totals: stuck causes N, useless escalations M.` footer that does not match the row counts. Exit 0 otherwise.
"""

import argparse
import os
import re
import sys

SECTIONS = ("Stuck causes", "Useless escalations")
HEAD_RE = re.compile(r"^## (.+?)\s*$")
TOTALS_RE = re.compile(r"^Totals: stuck causes (\d+), useless escalations (\d+)\.\s*$")
HEADER_CELL, DIVIDER_PREFIX = "stage", "---"
ROW_CELLS = 4  # stage | task | report | cause


def parse(text):
    """-> ({section: [cells, ...]}, (stuck, useless) totals or None). Header and divider rows are skipped."""
    sections: dict[str, list[list[str]]] = {}
    totals = None
    cur = None
    for ln in text.splitlines():
        head = HEAD_RE.match(ln)
        if head:
            cur = head.group(1)
            sections.setdefault(cur, [])
            continue
        found = TOTALS_RE.match(ln)
        if found:
            totals = (int(found.group(1)), int(found.group(2)))
        if cur in SECTIONS and ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells[0] == HEADER_CELL or cells[0].startswith(DIVIDER_PREFIX):
                continue
            sections[cur].append(cells)
    return sections, totals


def problems(path, check_files=True):
    """-> [message] for the retro lessons report at PATH; [] when it is good. CHECK_FILES False skips the report-file existence check (the cited reports live in git-ignored `.work`, so a clean checkout never has them)."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return [f"cannot read {path}: {exc.strerror}"]
    sections, totals = parse(text)
    out = []
    for name in SECTIONS:
        if name not in sections:
            out.append(f"missing section: ## {name}")
    for name in SECTIONS:
        for cells in sections.get(name, []):
            if len(cells) != ROW_CELLS or not all(cells):
                out.append(f"{name}: row needs {ROW_CELLS} non-empty cells: {cells}")
            elif check_files and not os.path.isfile(cells[2]):
                out.append(f"{name}: report file missing: {cells[2]}")
    if totals is None:
        out.append("missing totals footer: Totals: stuck causes N, useless escalations M.")
    else:
        rows = tuple(len(sections.get(name, [])) for name in SECTIONS)
        if totals != rows:
            out.append(f"totals {totals[0]}, {totals[1]} do not match rows {rows[0]}, {rows[1]}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--no-file-check", action="store_true", help="do not require the cited report files to exist")
    a = ap.parse_args(argv)
    found = problems(a.report, check_files=not a.no_file_check)
    for msg in found:
        print(f"FAIL: {msg}")
    if found:
        return 1
    print(f"OK: {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
