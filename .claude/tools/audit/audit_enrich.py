#!/usr/bin/env python3
"""audit_enrich: best-effort check of "unused code" audit findings with `lp nav refs`, written into the findings file as `check`.

    python3 -I /root/.claude/tools/audit/audit_enrich.py <repo> [--findings FILE] [--verdict]

A finding counts as an unused-code claim when its rule or message says unused/dead/never used and quotes a name.
Tags only, never drops a finding. No answer from the language server just leaves the finding without a `check`."""

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from codenames import code_name
from search.lpcall import lp_rc

CLAIM_WORDS = ("unused", "dead", "never used", "never read", "never called", "never constructed", "not used", "unreferenced")


def claims_unused(f):
    t = (f.get("rule", "") + " " + f.get("message", "")).lower()
    return any(w in t for w in CLAIM_WORDS)


def quoted_tokens(msg):
    """Single-word tokens inside '...', "..." or `...` in an audit message."""
    out = []
    for q in "'\"`":
        parts = msg.split(q)
        out += [p for p in parts[1::2] if p and " " not in p and len(p) <= 120]
    return out


MAX_CHECKS, BUDGET_S = 40, 240


def refs(repo, file, name):
    rc, out = lp_rc(repo, "nav", "refs", file, name, t=40)
    if rc != 0:
        return None
    raw = [x for x in out.splitlines() if x.strip()]
    text = any("word-match" in x for x in raw)
    hits = [x for x in raw if not x.startswith("#") and x != "(none)"]
    used = [x for x in hits if not x.startswith(file + ":")]
    if text:
        return f"{len(hits)} text hits, {len(used)} in other files (unverified word search, not the same symbol for sure)"
    return f"{len(hits)} refs incl. the definition, {len(used)} in other files" + (
        ": " + ", ".join(x.split()[0] for x in used[:4]) if used else " (supports the claim)"
    )


def _newest_findings(repo):
    """The most recent raw findings file of the repo, None when there is none."""
    return next(iter(sorted(glob.glob(os.path.join(repo, ".audits", "raw", "*findings.json")), key=os.path.getmtime, reverse=True)), None)


def _check_of(repo, f):
    """The `check` text for one finding, None when it makes no checkable unused-code claim or `lp` has no answer."""
    if "check" in f or not claims_unused(f):
        return None
    name = next((n for n in (code_name(t) for t in quoted_tokens(f.get("message", ""))) if n), None)
    if not name or not f.get("file"):
        return None
    return refs(repo, f["file"], name)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--findings")
    ap.add_argument("--verdict", action="store_true")
    a = ap.parse_args(argv)
    path = a.findings or _newest_findings(a.repo)
    if not path:
        print(("VERDICT: DONE\n" if a.verdict else "") + "audit-enrich: no findings file")
        return 0
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    fl = d["findings"] if isinstance(d, dict) else d
    n, t0 = 0, time.time()
    for f in fl:
        if n >= MAX_CHECKS or time.time() - t0 > BUDGET_S:
            break
        c = _check_of(a.repo, f)
        if c:
            f["check"] = c
            n += 1
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    print(("VERDICT: DONE\n" if a.verdict else "") + f"audit-enrich: {n} unused-code findings checked in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
