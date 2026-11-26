"""related_files: the audit context's "Related, unchanged" part: files NOT in the diff that a change can break (callers, tests, contracts, locks, data).

The local model (Strata) names candidates once per group of changed files, from the repo file list; code keeps names that exist and are not in the diff,
ranks the most-named first and checks each with one cheap fact (a changed symbol or the changed module name appears in the file). Unverified names are dropped.
Strata down: `related_unchanged` returns None (the caller prints a one-line note)."""

import hashlib
import json
import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
from localmodel import plan_context as pc  # noqa: E402

MAX_LINES, MAX_CHARS, MAX_GROUPS, MAX_SYMS = 10, 1500, 4, 25
ASK = (
    "\n\nA change touched the files and added the symbols below. Name up to {n} OTHER existing files from the list above that this change can break: "
    "callers, tests, contracts, lock or data files. ONE json object only: " + '{{"files": ["path", ...]}}\n\nCHANGED:\n'
)


def groups(changed, diff_stat):
    """[(paths, symbols)] one per top-level folder of the changed files, biggest groups first, at most MAX_GROUPS."""
    by: dict = {}
    for f in sorted(changed):
        by.setdefault(f.split("/")[0] if "/" in f else ".", []).append(f)
    out = [(fs, [s for f in fs for s in diff_stat.get(f, [])][:MAX_SYMS]) for fs in by.values()]
    return sorted(out, key=lambda g: -len(g[0]))[:MAX_GROUPS]


def _ask(strata, listing, cfg, paths, syms):
    """Names the model gives for one group, [] when its answer has no json object."""
    part = "\n".join(paths[:40]) + "\nsymbols: " + " ".join(syms)
    prompt = pc.NAMING_HEAD + "\n".join(listing) + ASK.format(n=cfg["per_section"]) + part
    ans = strata.strata_call(prompt, max_tokens=1500)
    try:
        return json.JSONDecoder().raw_decode(ans[ans.index("{") :])[0].get("files", [])
    except (ValueError, AttributeError):
        return []


def _load_cache(cache_file):
    try:
        with open(cache_file, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def candidates(root, listing, changed, diff_stat, cfg):
    """Names that exist in the listing and are not in the diff, most-named first (one model call per group; answers cached like the planner's)."""
    from search import strata

    known, gone = set(listing), set(changed)
    votes: dict = {}
    cache_file = os.path.join(root, ".code-index", "ctx-cache.json")
    cache = _load_cache(cache_file)
    lh = hashlib.sha1("\n".join(listing).encode(), usedforsecurity=False).hexdigest()
    for paths, syms in groups(changed, diff_stat):
        key = pc.naming_key(lh, cfg, "related:" + ASK + "\n".join(paths) + " ".join(syms))
        if key not in cache:
            cache[key] = _ask(strata, listing, cfg, paths, syms)
        for f in dict.fromkeys(cache[key]):
            if f in known and f not in gone:
                votes[f] = votes.get(f, 0) + 1
    pc._save_cache(cache_file, cache)
    return sorted(votes, key=lambda f: -votes[f])


def fact(root, f, changed, diff_stat):
    """One cheap mechanical fact tying file F to the change: 'uses sym' or 'mentions module', else ''."""
    try:
        with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
            text = fh.read(200000)
    except OSError:  # a candidate that is gone or unreadable stays listed, it just has no fact line
        return ""
    for c in changed:
        for s in diff_stat.get(c, []):
            if re.search(rf"\b{re.escape(s)}\b", text):
                return f"uses {s} ({c})"
    for c in changed:
        stem = os.path.splitext(os.path.basename(c))[0]
        if len(stem) > 2 and re.search(rf"\b{re.escape(stem)}\b", text):
            return f"mentions {stem} ({c})"
    return ""


def related_unchanged(repo, changed, diff_stat, cfg=None):
    """-> ["- file: fact", ...] (at most MAX_LINES lines, MAX_CHARS chars), [] when nothing verified; None when the local model is off or down."""
    from search.strata import strata_up

    cfg = cfg or pc.CTX_DEFAULTS
    if not changed:
        return []
    if not cfg.get("model") or not strata_up():
        return None
    listing = pc._listed_files(repo, cfg)
    if listing is None:
        return None
    out, size = [], 0
    for f in candidates(repo, listing, changed, diff_stat, cfg):
        why = fact(repo, f, changed, diff_stat)
        line = f"- {f}: {why}"
        if why and size + len(line) <= MAX_CHARS:
            out.append(line)
            size += len(line)
        if len(out) >= MAX_LINES:
            break
    return out
