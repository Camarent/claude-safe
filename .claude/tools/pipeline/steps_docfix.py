#!/usr/bin/env python3
"""Docs fix by script: a reviewer FAIL that repeats one docs-only one-line finding (an `old -> new` pair) in two review rounds is applied by
script, then the reviewer runs again. The finding line in the reviewer report:

    - `docs/file.md`: `old text` -> `new text`

The pair is applied only when `old text` occurs exactly once in the file; otherwise nothing changes and the normal review round runs.
Prints `VERDICT: DONE` when the pair was applied and committed in the job worktree, `VERDICT: FAIL` plus the reason otherwise.

    python3 -I /root/.claude/tools/pipeline/steps_docfix.py --file PATH --old OLD --new NEW

Imported by steps.py for the dispatch line; run as a script by the driver (cwd: the job worktree)."""

import argparse
import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir; the imports below need the tools dir
from pipeline import gitx  # noqa: E402
from pipeline.configuration import DONE, FAIL, VERDICT_PREFIX  # noqa: E402

DOCFIX_WHY = "docfix: repeated docs-only one-line finding applied by script, then the reviewer runs again"
DOCFIX_LINE = re.compile(r"^\s*[-*]?\s*`([^`\n]+)`:\s*`([^`\n]+)`\s*->\s*`([^`\n]+)`\s*$", re.M)
DOCFIX_SUBJECT = "docs({file}): one-line fix from a repeated review finding"


def finding_lines(text):
    """-> (line, path, old, new) for every docfix finding line of a review report, in order."""
    return [(m.group(0).strip(), m.group(1), m.group(2), m.group(3)) for m in DOCFIX_LINE.finditer(text)]  # group(0) is never None on a match


def repeated_pair(prev_text, cur_text):
    """-> (path, old, new) when both review reports carry exactly one docfix line and it is the same line; else None."""
    prev, cur = finding_lines(prev_text), finding_lines(cur_text)
    if len(cur) != 1 or prev != cur:
        return None
    return cur[0][1:]


def apply_pair(path, old, new, root="."):
    """-> (applied, reason). Replaces the one occurrence of `old` in the file under ROOT; any other count changes nothing."""
    if not old or os.path.isabs(path) or ".." in path.split("/"):
        return False, f"not applied: path or pair not usable ({path})"
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return False, f"not applied: no such file {path}"
    with open(full, encoding="utf-8") as f:
        text = f.read()
    n = text.count(old)
    if n != 1:
        return False, f"not applied: `{old}` occurs {n} times in {path}, needs exactly 1"
    with open(full, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return True, f"applied: 1 occurrence in {path}"


def commit(root, path):
    """-> error text, or '' when the one file is committed on the job branch."""
    r = gitx.run(root, "add", "--", path)
    if r.returncode:
        return f"commit failed: git add rc {r.returncode}"
    r = gitx.run(root, "commit", "-q", "-m", DOCFIX_SUBJECT.format(file=path), "--", path)
    return "" if r.returncode == 0 else f"commit failed: rc {r.returncode}"


def main(argv=None):
    p = argparse.ArgumentParser(description="Docs fix by script: apply one repeated one-line finding (old -> new) in the job worktree.")
    p.add_argument("--file", required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    a = p.parse_args(argv)
    ok, why = apply_pair(a.file, a.old, a.new)
    if ok:
        err = commit(".", a.file)
        ok, why = (not err, err or why)
    print(f"{VERDICT_PREFIX}{DONE if ok else FAIL}")
    print(why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
