#!/usr/bin/env python3
"""find_eval: golden questions for `lp find`; hit@1, hit@3, IDK rate. Offline by default (--no-model keyword/symbol path).

  find_eval.py --repo REPO [--questions eval/find_eval.json] [--baseline eval/find_eval.baseline.json] [--model] [--write-baseline]

A question: {"q": "...", "expect": ["file", ...]}; a hit is any expected file among the first N result files. Exit 1 when hit@3 drops below the baseline.
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
from codenames import hit_file  # noqa: E402
from search.lpcall import lp  # noqa: E402


def ask(repo, q, model, t=120):
    out = lp(repo, "find", q, *([] if model else ["--no-model"]), t=t)
    files = []
    for line in out.splitlines():
        f = hit_file(line)
        if f and f not in files:
            files.append(f)
    return files


def evaluate(repo, questions, model=False):
    n = h1 = h3 = idk = 0
    misses = []
    for item in questions:
        files = ask(repo, item["q"], model)
        n += 1
        idk += not files
        h1 += any(f in item["expect"] for f in files[:1])
        ok3 = any(f in item["expect"] for f in files[:3])
        h3 += ok3
        if not ok3:
            misses.append((item["q"], files[:3]))
    return {"n": n, "hit1": round(h1 / n, 3) if n else 0, "hit3": round(h3 / n, 3) if n else 0, "idk": round(idk / n, 3) if n else 0}, misses


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--questions", default=os.path.join(HERE, "eval", "find_eval.json"))
    ap.add_argument("--baseline", default=os.path.join(HERE, "eval", "find_eval.baseline.json"))
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    res, misses = evaluate(a.repo, json.loads(Path(a.questions).read_text(encoding="utf-8")), a.model)
    print(json.dumps(res))
    for q, f in misses:
        print(f"MISS: {q} -> {', '.join(f) or '(nothing)'}")
    if a.write_baseline:
        with open(a.baseline, "w", encoding="utf-8") as f:
            json.dump(res, f)
        return 0
    try:
        base = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("no baseline")
        return 0
    if res["hit3"] < base["hit3"]:
        print(f"FAIL: hit@3 {res['hit3']} < baseline {base['hit3']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
