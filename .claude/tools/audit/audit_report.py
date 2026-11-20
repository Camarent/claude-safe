#!/usr/bin/env python3
"""audit_report: grouped counts and top-N findings from an audit_run findings.json."""

import argparse
import collections
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("findings")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--tool")
    ap.add_argument("--group", choices=["file", "rule"], default="rule")
    ap.add_argument("--list", action="store_true", help="one line per finding (top N), with the `check` from audit_enrich when present")
    a = ap.parse_args()
    with open(a.findings) as fh:
        d = json.load(fh)
    fl = d["findings"] if isinstance(d, dict) else d
    if a.tool:
        fl = [f for f in fl if f["tool"] == a.tool]
    if not fl:
        print("total 0: clean")
        return
    print(f"total {len(fl)}; by tool: {dict(collections.Counter(f['tool'] for f in fl))}")
    print("by severity: {}".format(dict(collections.Counter(f["severity"] for f in fl))))
    g = collections.Counter((f["tool"], f[a.group]) for f in fl)
    if a.list:
        for f in fl[: a.top]:
            print(
                "{}:{} {} {} {}{}".format(
                    f["file"], f.get("line", ""), f["tool"], f["rule"], f["message"][:100], ("  [check: {}]".format(f["check"])) if f.get("check") else ""
                )
            )
        return
    print(f"top {a.top} by {a.group}:")
    for (t, k), n in g.most_common(a.top):
        print(f"  {n:5d} {t} {k}")


if __name__ == "__main__":
    main()
