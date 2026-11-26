"""ctx_config: the `context` settings of pipeline.json, the index refresh and the repo roots a request touches. No model; pipeline and audit import this, not plan_context."""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from search.lpcall import lp

CTX_DEFAULTS: dict[str, Any] = {
    "ignore": [".cache/*", ".work*", "*.mp3", "content/*", "node_modules/*", "target/*", "dist/*", "cargo-target/*", ".gitignore", "*/.gitignore"],
    "per_section": 25,
    "model": True,
    "mode": "explore",  # "explore": local model explores throwaway checkouts; "batch": batch naming
    "index": [],
}


def ctx_config(workspace):
    """-> CTX_DEFAULTS overlaid with the `context` object of <workspace>/pipeline.json."""
    try:
        data = json.loads(Path(os.path.join(workspace, "pipeline.json")).read_text(encoding="utf-8")).get("context", {})
    except (OSError, ValueError, AttributeError):
        data = {}
    return {**CTX_DEFAULTS, **(data if isinstance(data, dict) else {}), "workspace": os.path.abspath(workspace)}


def ensure_index(workspace, cfg=None):
    """Refresh the code index of every git repo that `context.index` (globs relative to WORKSPACE, in pipeline.json) names, one after another.
    -> [(repo, seconds)]; a repo without .git is skipped, a failing `lp index` only costs its time."""
    import glob

    cfg = cfg or ctx_config(workspace)
    repos = sorted({d for g in cfg["index"] for d in glob.glob(os.path.join(workspace, g)) if os.path.exists(os.path.join(d, ".git"))})
    done = []
    for r in repos:
        if not os.path.isdir(os.path.join(r, ".code-index")):
            print(f"indexing {r}: first build, can take minutes", file=sys.stderr, flush=True)
        t = time.time()
        lp(r, "index", t=1800)
        done.append((r, round(time.time() - t)))
    return done


def indexed_repos(workspace, cfg):
    """Every git repo the `context.index` globs name (the repos `ensure_index` refreshes)."""
    import glob

    return sorted({d for g in cfg.get("index", []) for d in glob.glob(os.path.join(workspace, g)) if os.path.exists(os.path.join(d, ".git"))})


def pick_roots(repo, text, cfg=None):
    """[REPO, then every nested git repo a path in TEXT points into, then every other indexed repo]. Any course may hold the code a request touches, so each indexed repo gets a pack."""
    out = [repo]
    for d in indexed_repos((cfg or {}).get("workspace") or os.path.dirname(os.path.abspath(repo)), cfg or {}):
        if d not in out:
            out.append(d)
    for tok in re.findall(r"[\w.-]+(?:/[\w.-]+)+", text):
        n = pick_root(repo, tok)
        if n not in out:
            out.append(n)
    return out


def pick_root(repo, text):
    """The nested git repo under REPO that a path in TEXT points into (private courses are their own repo), else REPO."""
    best = repo
    for tok in re.findall(r"[\w.-]+(?:/[\w.-]+)+", text):
        for base in (repo, os.path.dirname(os.path.abspath(repo))):
            d = os.path.abspath(os.path.join(base, tok))
            while d != base and not os.path.isdir(d):
                d = os.path.dirname(d)
            while d.startswith(os.path.abspath(repo) + os.sep):
                if os.path.exists(os.path.join(d, ".git")):
                    if len(d) > len(best):
                        best = d
                    break
                d = os.path.dirname(d)
    return best
