#!/usr/bin/env python3
"""index_step: script step `index-refresh`. Runs `local.py index` in the project root and in each nested repo that has a `.code-index`.

    python3 -I /root/.claude/tools/index_step.py --dir D

After the runs it shrinks every `.code-index/index.db` under the project that is mostly free pages (a past big build leaves the file large: SQLite never gives the space back by itself).
Prints `VERDICT: DONE` (every run exited 0) or `VERDICT: FAIL` first, then one line per run: `<root>: rc N` plus the last output line, then one `vacuumed` line per shrunk file."""

import argparse
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline.configuration import INDEX_TIMEOUT_S, VERDICT_PREFIX
from pipeline.util import add_common_args, script_timeout

LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search", "local.py")


def roots(d):
    """The project root, then each direct child repo that holds a `.code-index`."""
    out = [d]
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isdir(os.path.join(p, ".code-index")):
            out.append(p)
    return out


VACUUM_MIN_BYTES = 50 * 1024 * 1024  # smaller files are not worth the pass
VACUUM_FREE_SHARE = 0.25  # shrink when this share of the pages is free
SKIP_WALK = {".git", "node_modules", ".wt", ".work", "target", ".venv"}
MAX_DEPTH = 3  # project/repo/workspace/repo


def index_dbs(d):
    """Every `.code-index/index.db` at most MAX_DEPTH folders below D."""
    out = []
    for cur, dirs, names in os.walk(d):
        if cur[len(d) :].count(os.sep) >= MAX_DEPTH:
            dirs[:] = []
        if os.path.basename(cur) == ".code-index":
            dirs[:] = []
            if "index.db" in names:
                out.append(os.path.join(cur, "index.db"))
            continue
        dirs[:] = sorted(x for x in dirs if x not in SKIP_WALK)
    return out


def vacuum_bloated(db):
    """VACUUM DB when it is big and mostly free pages. -> 'saved N MB' line, or '' when skipped. A busy or unreadable file is left alone (best effort)."""
    try:
        if os.path.getsize(db) < VACUUM_MIN_BYTES:
            return ""
        con = sqlite3.connect(db, timeout=5)
        try:
            free, total = con.execute("pragma freelist_count").fetchone()[0], con.execute("pragma page_count").fetchone()[0]
            if not total or free / total < VACUUM_FREE_SHARE:
                return ""
            before = os.path.getsize(db)
            con.execute("vacuum")
        finally:
            con.close()
        return f"{db}: vacuumed, saved {(before - os.path.getsize(db)) // (1024 * 1024)} MB"
    except (OSError, sqlite3.Error):
        return ""


def _timeout():
    """Seconds one index run may take: env ORCH_SCRIPT_TIMEOUT, default INDEX_TIMEOUT_S."""
    return script_timeout(INDEX_TIMEOUT_S)


def main():
    ap = argparse.ArgumentParser()
    add_common_args(ap)
    d = os.path.abspath(ap.parse_args().dir)
    lines, bad = [], False
    for r in roots(d):
        try:
            p = subprocess.run([sys.executable, "-I", LOCAL, "index", "--root", r], cwd=r, capture_output=True, text=True, timeout=_timeout(), check=False)
            rc, out = p.returncode, (p.stdout + p.stderr).strip()
        except subprocess.TimeoutExpired:
            rc, out = 124, f"timed out after {_timeout():g}s (ORCH_SCRIPT_TIMEOUT)"
        except OSError as e:
            rc, out = 127, f"cannot run: {e}"
        tail = out.split("\n")[-1:]
        lines.append(f"{r}: rc {rc} {tail[0] if tail else ''}")
        bad = bad or rc != 0
    lines += [x for x in (vacuum_bloated(db) for db in index_dbs(d)) if x]
    print(VERDICT_PREFIX + ("FAIL" if bad else "DONE"))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
