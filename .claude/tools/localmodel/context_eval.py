#!/usr/bin/env python3
"""context_eval: scores the plan-context file naming against a reference list, and checks the naming cache.

    python3 -I /root/.claude/tools/localmodel/context_eval.py GOLD.json [--runs 3] [--check-cache] [--quick] [--out FILE]

GOLD.json = {"repo", "prompt" (file next to it), "files": [existing paths the real plan needed]}. Cold runs bypass the cache and leave it alone.
Prints one `EVAL cold ...` line (recall per run, spread, files named, seconds), one `EVAL meta ...` line (Strata model id, naming prompt hash, file-list size, UTC date; also under "meta" in --out), and with --check-cache one `EVAL cache ...` line:
per cached request line, how much a fresh answer overlaps the cached one, against how much two fresh answers overlap each other (the model's own noise).
--quick: one run, only the first 3 request lines of the prompt; prints `EVAL quick (not comparable to a full run)`; the gold stays whole, so its recall is NOT comparable to a full run. Exit 3 and `EVAL SKIPPED` when Strata is down."""

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
import plan_context as pc  # noqa: E402
from search.strata import strata_model  # noqa: E402


def recall(gold, named):
    """(hits, total) of the reference files found in NAMED."""
    return sum(1 for g in gold if g in set(named)), len(gold)


def overlap(a, b):
    """Jaccard overlap of two name lists; 1.0 when both are empty."""
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def spread(values):
    return (min(values), max(values)) if values else (0, 0)


def cold_runs(root, text, cfg, gold, runs):
    out = []
    for _ in range(runs):
        t = time.time()
        named = pc.model_files(root, text, cfg, use_cache=False)
        h, n = recall(gold, named)
        out.append({"hits": h, "total": n, "named": len(named), "secs": round(time.time() - t)})
    return out


def cache_check(root, text, cfg):
    """-> [(section, cached_names, fresh_names, fresh2_names)] for the sections whose answer is in the cache now.
    fresh2 is a second cold answer: how much two fresh answers differ is the baseline for how much a cached answer may differ from a fresh one."""
    pc.model_files(root, text, cfg, use_cache=True)  # fills the cache for lines it does not hold yet
    per: list[tuple] = []
    pc.model_files(root, text, cfg, use_cache=True, per=per)  # now every line is a hit
    cached = [(p, n) for p, n, hit in per if hit]
    fresh: list[tuple] = []
    fresh2: list[tuple] = []
    pc.model_files(root, text, cfg, use_cache=False, per=fresh)
    pc.model_files(root, text, cfg, use_cache=False, per=fresh2)
    f1, f2 = {p: n for p, n, _ in fresh}, {p: n for p, n, _ in fresh2}
    return [(p, n, f1.get(p, []), f2.get(p, [])) for p, n in cached]


QUICK_LINES = 3


def eval_meta(root, cfg):
    """What the score was measured with: model id, short hash of the naming prompt, file-list size, UTC date."""
    prompt = (pc.NAMING_HEAD + pc.NAMING_ASK).encode()
    return {
        "model": strata_model(),
        "prompt_hash": hashlib.sha1(prompt, usedforsecurity=False).hexdigest()[:10],
        "files_listed": len(pc._listed_files(root, cfg) or []),
        "date": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--check-cache", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    g = json.loads(Path(a.gold).read_text(encoding="utf-8"))
    text = Path(os.path.join(os.path.dirname(os.path.abspath(a.gold)), g["prompt"])).read_text(encoding="utf-8")
    cfg = pc.ctx_config(g.get("workspace", os.getcwd()))
    from search.strata import strata_up

    if not strata_up():
        print("EVAL SKIPPED: local model (Strata) is down", flush=True)
        return 3
    runs, lines = a.runs, None
    if a.quick:
        runs, lines = 1, pc.sections(text)[:QUICK_LINES]
        text = "\n".join(lines)
        print("EVAL quick (not comparable to a full run)", flush=True)
    res = {"gold": a.gold, "cold": cold_runs(g["repo"], text, cfg, g["files"], runs), "meta": eval_meta(g["repo"], cfg)}
    if lines is not None:
        res["quick_lines"] = len(lines)
    hs = [r["hits"] for r in res["cold"]]
    lo, hi = spread(hs)
    print(
        f"EVAL cold: recall {'/'.join(str(h) for h in hs)} of {len(g['files'])} (spread {lo}-{hi}), named {res['cold'][-1]['named']}, secs {'/'.join(str(r['secs']) for r in res['cold'])}",
        flush=True,
    )
    m = res["meta"]
    extra = f", request lines {len(lines)}" if lines is not None else ""
    print(f"EVAL meta: model {m['model']}, prompt {m['prompt_hash']}, files listed {m['files_listed']}, date {m['date']}{extra}", flush=True)
    if a.check_cache:
        rows = cache_check(g["repo"], text, cfg)
        cv = [overlap(c, f) for _, c, f, _ in rows]
        ff = [overlap(f, f2) for _, _, f, f2 in rows]

        def mean(x):
            return sum(x) / len(x) if x else 1.0

        res["cache"] = {"sections": len(rows), "cached_vs_fresh": [round(x, 2) for x in cv], "fresh_vs_fresh": [round(x, 2) for x in ff]}
        verdict = "consistent with the model's own noise" if mean(cv) >= mean(ff) - 0.1 else "LOWER than the model's own noise: check the cache key"
        print(
            f"EVAL cache: {len(rows)} cached lines, overlap cached-vs-fresh mean {mean(cv):.2f} (min {min(cv, default=1):.2f}), fresh-vs-fresh mean {mean(ff):.2f} (min {min(ff, default=1):.2f}): {verdict}",
            flush=True,
        )
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
