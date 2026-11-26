#!/usr/bin/env python3
"""plan_context: exact, no-model context for the planner and the auditors. Best effort: whatever `lp` cannot answer is left out.

  plan_context.py goal  <repo> --text-file GOAL.md --out FILE        code leads for a request, before the architect starts
  plan_context.py index --workspace W                                 refresh the index of the repos W/pipeline.json `context.index` names
  plan_context.py stage <repo> --stage-dir .work/S --base B --head H --out FILE [--verdict]
                                                                     what the plan said vs what changed, before the auditors

`goal`: `lp find` on the request, `nav symbols` for names it marks as code, the outline of the top files, tests that mention them; when the local model (Strata)
answers, it also names the files a planner must see, one pass per request section over the repo's file list (`context` in pipeline.json: ignore, per_section, model).
The repo is the nested git repo (a private repo inside the project) when the request names a path inside one.
`stage`: per task its goal, Predicted paths and Done line; changed files no task covers; predicted paths nothing changed."""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
from codenames import hit_file  # noqa: E402
from pipeline import gitx  # noqa: E402
from pipeline import phase_context as _phase  # noqa: E402
from pipeline.configuration import WORK_DIR  # noqa: E402
from pipeline.phase_context import _plan_rows, _read  # noqa: E402,F401
from pipeline.plan_files import PATH_FILE  # noqa: E402
from pipeline.stage_recall import (  # noqa: E402,F401
    all_recall,
    context_paths,
    covered,
    find_context_file,
    live_recall,
    modified_files,
    named_count,
    pack_paths,
    pack_sections,
    stage_context,
    task_info,
)
from pipeline.util import swallowed  # noqa: E402
from search.ctx_config import CTX_DEFAULTS, ctx_config, ensure_index, indexed_repos, pick_root, pick_roots  # noqa: E402,F401
from search.leadparts import outline_lines, symbol_lines  # noqa: E402
from search.leads_docs import (  # noqa: E402,F401
    DESCRIBED,
    code_files,
    digest,
    doc_mentions,
    group_of,
    top_line,
    with_docs,
    with_tests,
)
from search.lpcall import git_text, lp  # noqa: E402
from search.strata import strata_model  # noqa: E402


def sections(text):
    """Lines of at least 6 words; the whole text when there are none."""
    return [line.strip() for line in text.splitlines() if len(line.split()) >= 6] or [text.strip()]


NAMING_HEAD = "Files of a repo:\n"
NAMING_ASK = (
    "\n\nA planner must write a task plan for this goal and will NOT explore the code. Name up to {n} EXISTING files from the list above that he must read "
    'or that will change (code, config, tests, contracts, locks, data schemas). ONE json object only: {{"files": ["path", ...]}}\n\nGOAL:\n'
)


def naming_key(listing_hash, cfg, part):
    """Cache key of one naming answer: everything that can change it (local model id, prompt text, list size, file list, the request line)."""
    ident = "|".join([strata_model(), NAMING_HEAD, NAMING_ASK, str(cfg["per_section"]), listing_hash, part])
    return hashlib.sha1(ident.encode(), usedforsecurity=False).hexdigest()


BATCH_ASK = (
    "\n\nA planner must write a task plan for the numbered request lines below and will NOT explore the code. Name up to {n} EXISTING files from the list above "
    "that he must read or that will change (code, config, tests, contracts, locks, data schemas). Order them by importance for the whole request, most important first. "
    'For each file give the numbers of the request lines it serves. If this repo has nothing to do with the request lines, answer {{"files": []}}. ONE json object only: {{"files": [{{"path": "path", "lines": [1, 2]}}, ...]}}\n\nREQUEST LINES:\n'
)


BATCH_THINKING = True  # False: the batch naming call sends thinking disabled


def _name_batch(strata, fs, cfg, parts):
    """[(path, [line numbers])] most important first, from ONE model call over all request lines; None when the answer holds no usable json."""
    body = "\n".join(f"{i}. {p[:600]}" for i, p in enumerate(parts, 1))
    ans = strata.strata_call(
        NAMING_HEAD + "\n".join(fs) + BATCH_ASK.format(n=min(cfg["per_section"] * 2, 60)) + body[:12000], max_tokens=16000, thinking=BATCH_THINKING
    )
    try:
        items = json.JSONDecoder().raw_decode(ans[ans.index("{") :])[0].get("files", [])
        return [(i["path"], [n for n in i.get("lines", []) if isinstance(n, int)]) for i in items if isinstance(i, dict) and isinstance(i.get("path"), str)]
    except (ValueError, AttributeError, TypeError):  # KEEP: no usable json is the documented None answer
        return None


def _listed_files(root, cfg):
    """Files of the repo the naming step may offer (git list minus the index folder and `ignore` globs); None when git cannot run."""
    try:
        fs = gitx.run(root, "ls-files", "-z", "-co", "--exclude-standard").stdout.split("\0")
    except (OSError, subprocess.SubprocessError):  # KEEP: git cannot run is the documented None answer
        return None
    return [f for f in fs if f and not f.startswith(".code-index/") and not any(fnmatch.fnmatch(f, g) for g in cfg["ignore"])]


def _path_of(item):
    """A named file from one model answer item: a path string, or a dict with a string `path`; None for anything else."""
    p = item.get("path") if isinstance(item, dict) else item
    return p if isinstance(p, str) else None


def _name_section(strata, fs, cfg, part):
    """The files the model names for one request section; None when its answer holds no json object."""
    ans = strata.strata_call(NAMING_HEAD + "\n".join(fs) + NAMING_ASK.format(n=cfg["per_section"]) + part[:6000], max_tokens=1500)
    try:
        return json.JSONDecoder().raw_decode(ans[ans.index("{") :])[0].get("files", [])
    except (ValueError, AttributeError):  # KEEP: no usable json is the documented None answer
        return None


def _save_cache(cache_file, cache):
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(cache_file + ".tmp", cache_file)
    except OSError as ex:
        swallowed("plan_context._save_cache", ex)  # the cache is an optimisation: a write failure only costs a rebuild


def _load_cache(cache_file, use_cache):
    try:
        return json.loads(Path(cache_file).read_text(encoding="utf-8")) if use_cache else {}
    except (OSError, ValueError):
        return {}


def _batch_files(strata, fs, cfg, parts, listing, cache, per):
    """Ranked files from one batched model call (cached), or None when the batch gave nothing."""
    known = set(fs)
    key = naming_key(listing, cfg, "BATCH\n" + "\n".join(parts))
    got = cache.get(key)
    hit = got is not None
    if got is None:
        got = _name_batch(strata, fs, cfg, parts)
        if got is not None:
            cache[key] = got
    if got is None:
        return None
    got = [(p, ns) for p, ns in got if p in known]
    if per is not None:
        per += [(part, [p for p, ns in got if i in ns], hit) for i, part in enumerate(parts, 1)]
    return list(dict.fromkeys(p for p, _ in got))  # the model's order is the ranking


FALLBACK_BUDGET_S = (
    300  # wall clock for the one-call-per-section fallback of one repo: past it the remaining sections are skipped (each call may wait for a localq slot)
)


def _section_votes(strata, fs, cfg, parts, listing, cache, per):
    """One model call per section (cached); votes per existing file. Stops asking after FALLBACK_BUDGET_S (cached sections still count)."""
    known = set(fs)
    votes: dict = {}
    t0 = time.time()
    for part in parts:
        key = naming_key(listing, cfg, part)
        names = cache.get(key)
        hit = names is not None
        if names is None and time.time() - t0 > FALLBACK_BUDGET_S:
            print(f"plan_context: fallback budget {FALLBACK_BUDGET_S}s spent, skipping the remaining sections", file=sys.stderr, flush=True)
            break
        if names is None:
            names = _name_section(strata, fs, cfg, part)
            if names is not None:
                cache[key] = names
        names = [n for n in dict.fromkeys(_path_of(x) for x in names or []) if n in known]
        if per is not None:
            per.append((part, names, hit))
        for f in names:
            votes[f] = votes.get(f, 0) + 1
    return votes


def model_files(root, text, cfg, use_cache=True, per=None):
    """Existing files the local model names, section by section, most-named first. [] when Strata is down or answers nothing.
    use_cache=False asks the model for every section and leaves the cache file alone. PER, a list, receives (section, names, from_cache) per section."""
    from search import strata
    from search.strata import strata_up

    if not cfg.get("model") or not strata_up():
        return []
    fs = _listed_files(root, cfg)
    if fs is None:
        return []
    cache_file = os.path.join(root, ".code-index", "ctx-cache.json")
    cache = _load_cache(cache_file, use_cache)
    listing = hashlib.sha1("\n".join(fs).encode(), usedforsecurity=False).hexdigest()
    parts = sections(text)
    ranked = None
    if cfg.get("batch", True) and len(parts) > 1:
        ranked = _batch_files(strata, fs, cfg, parts, listing, cache, per)
    if ranked is None:
        votes = _section_votes(strata, fs, cfg, parts, listing, cache, per)
        ranked = sorted(votes, key=lambda f: -votes[f])  # most-named first, ties in first-named order
    if use_cache:
        _save_cache(cache_file, cache)
    return ranked


OUTLINED = 6  # code files whose symbols are listed


def _test_leads(repo, files):
    """Test-looking files `lp find` returns for the first three FILES."""
    tests = []
    for f in files[:3]:
        stem = os.path.splitext(os.path.basename(f))[0]
        for x in lp(repo, "find", f"tests for {stem}", "--no-model").splitlines():
            hf = hit_file(x)
            if hf and ("test" in hf.lower() or "spec" in hf.lower()) and hf not in tests:
                tests.append(hf)
    return tests


def _lead_sections(repo, text, t0):
    """Sections from `lp`: find, symbols, outline of the top files, tests. -> (lines, t_index)."""
    lines = ["# Code context for this request (by script, lp)", "", "Unverified leads: confirm in code. `keyword` hits are a word search, not proof."]
    lp(repo, "index", t=1200)  # incremental; a nested private repo has no index until this runs
    t_index = round(time.time() - t0)
    q = " ".join(text.split())[:400]
    found = [x for x in lp(repo, "find", q, t=90).splitlines() if x.strip()]
    hits = [x for x in found if hit_file(x)]
    lines += ["", "## lp find"] + (["    " + x for x in found[:15]] or ["    (no answer)"])
    sym = symbol_lines(repo, text)
    if sym:
        lines += ["", "## symbols named in the request (nav symbols)", *sym]
    files = []
    for h in hits:
        f = hit_file(h)
        if f not in files:
            files.append(f)
    out = outline_lines(repo, files[:5])
    if out:
        lines += ["", "## outline of the top files", *out]
    tests = _test_leads(repo, files)
    if tests:
        lines += ["", "## tests that mention those files (lp find, keyword)"] + ["- " + t for t in tests[:10]]
    return lines, t_index


def _goal_one(repo, text, cfg):
    t0 = time.time()
    lines, t_index = _lead_sections(repo, text, t0)
    t1 = time.time()
    named = model_files(repo, text, cfg)
    lines += ["", f"## files the local model names for each request section (repo {os.path.basename(repo)}; exist, read the ones you need)"]
    lines += digest(repo, with_tests(repo, with_docs(repo, named))) or ["(local model offline or silent: none)"]
    out = outline_lines(repo, [f for f in named if f.endswith((".py", ".js", ".rs", ".ts"))][:OUTLINED])
    if out:
        lines += ["", "## outline of the first named files", *out]
    lines.append(f"\n(built in {round(time.time() - t0)} s: index {t_index} s, local model {round(time.time() - t1)} s)")
    return lines


def _explore_one(root, files, whys):
    lines = ["", f"## files the local model found by exploring (repo {os.path.basename(root)}; exist, read the ones you need)"]
    named = list(with_tests(root, with_docs(root, files)))
    lines += digest(root, named) or ["(none)"]
    if whys:
        lines += ["", "## why each file was picked"]
        for cat in dict.fromkeys(c for _, _, c in whys):  # grouped by the model's category, in its order
            got = [f"- {p}: {w}" for p, w, c in whys if w and c == cat]
            if got:
                lines += ([f"### {cat}"] if cat else []) + got[:30]
    out = outline_lines(root, [f for f in files if f.endswith((".py", ".js", ".rs", ".ts"))][:OUTLINED])
    if out:
        lines += ["", "## outline of the first named files", *out]
    return lines


def _write_explore_log(log, got, path):
    """Best effort: the explorer's log, its final answer and its stats as text at PATH (turns, tool calls, results)."""
    if not path:
        return
    try:
        from localmodel import explore as ex

        body = [
            *log,
            "",
            "answer: " + ("none" if got is None else "\n".join(f"  {c}: {r}/{p} - {w}" for r, p, w, c in got)),
            "",
            "stats: " + json.dumps(ex.agent_files.STATS, default=str),
        ]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        Path(path).write_text("\n".join(body) + "\n", encoding="utf-8")
    except Exception as err:  # a log never fails the planner
        swallowed("plan_context.log", err)


SEED_DEADLINE_S = 60  # all of seed_leads (every repo, every call) stops after this
SEED_FIND = 12  # files of `lp find` kept per repo as explorer leads
SEED_LOG = 10  # co-changed files (git log of the paths the request names) kept per repo
SEED_COMMITS = 40  # commits of the one `git log` over the named paths
REQUEST_PATH_RE = re.compile(rf"(?<![\w./-])({PATH_FILE})(?![\w/])")


def _co_changed(root, mine, end):
    """Files that changed in the last SEED_COMMITS commits touching the paths MINE (most often first), within the deadline END (monotonic)."""
    if not mine:
        return []
    # the pathspec of `git log` also filters --name-only: commits first, then their files in one `git show`
    hashes = git_text(["log", f"-n{SEED_COMMITS}", "--format=%H", "--", *mine], root, max(1, min(20, end - time.monotonic()))).split()
    files = git_text(["show", "--name-only", "--format=", *hashes], root, max(1, min(20, end - time.monotonic()))) if hashes else ""
    co: dict = {}
    for f in files.splitlines():
        if f and f not in mine and os.path.exists(os.path.join(root, f)):
            co[f] = co.get(f, 0) + 1
    return sorted(co, key=lambda f: -co[f])[:SEED_LOG]


def seed_leads(repos, text):
    """Text for the explorer's `leads` slot, no model: per repo the files `lp find --no-model` returns for the request, and the files that changed in the last commits together with the paths the request names (`git log`). The explorer then checks files instead of searching blind. "" when there is nothing."""
    q = " ".join(text.split())[:400].lstrip("-")  # a leading '-' would read as an option of `lp find`
    named = list(dict.fromkeys(REQUEST_PATH_RE.findall(text)))
    out, end = [], time.monotonic() + SEED_DEADLINE_S
    for name, root in repos.items():
        left = end - time.monotonic()
        if left <= 0:
            break
        found = list(dict.fromkeys(f for f in (hit_file(x) for x in lp(root, "find", q, "--no-model", t=min(60, left)).splitlines()) if f))[:SEED_FIND]
        mine = [n for n in named if os.path.exists(os.path.join(root, n))][:8]
        co_files = _co_changed(root, mine, end)
        if found or mine or co_files:
            out += [
                f"## repo {name}",
                "lp find: " + ", ".join(found),
                "named in the request: " + ", ".join(mine),
                "changed together with them (git log): " + ", ".join(co_files),
            ]
    return "\n".join(out)


def explore_context(repo, text, cfg, explore_log=None):
    """plan-context text from the local model's exploration of every picked repo; None when it fails (caller falls back to batch naming)."""
    from localmodel import explore as ex

    t0 = time.time()
    roots = pick_roots(repo, text, cfg)
    repos = {os.path.basename(r.rstrip("/")): r for r in roots}
    log: list = []
    got = ex.explore(repos, text, log=log, leads=seed_leads(repos, text))
    _write_explore_log(log, got, explore_log)
    st = ex.agent_files.STATS
    if not got:  # None (failed) or [] (answered with nothing): the caller runs the fallback
        if got is not None:
            st["ended"] = "empty"
        return None
    lines = ["# Code context for this request (by script and a local model that explored the repos)", "", "Unverified leads: confirm in code."]
    if st.get("ended") not in (None, "answer"):  # the loop was cut off: what it had found so far
        lines.append(
            f"PARTIAL: the explorer stopped on its limit ({st['ended']}); this is its best attempt, not a complete list. Search for what is missing yourself."
        )
    for name, root in repos.items():
        mine = [(p, w, c) for r, p, w, c in got if r == name]
        if not mine:
            continue  # a repo the explorer left alone (out of scope)
        head = f"# === repo {name} ({'root' if root == repo else 'nested, paths relative to it'}) ==="
        lines += [head, *_explore_one(root, [m[0] for m in mine], mine), ""]
    lines.append(f"\n(built in {round(time.time() - t0)} s: index 0 s, local model {round(time.time() - t0)} s)")
    return "\n".join(lines) + "\n"


def goal_context(repo, text, cfg=None, explore_log=None):
    cfg = cfg or CTX_DEFAULTS
    if cfg.get("mode", "explore") == "explore" and os.environ.get("STRATA") != "0":  # STRATA=0: model off, no explorer call
        got = explore_context(repo, text, cfg, explore_log)
        if got:
            return got
    roots = pick_roots(repo, text, cfg)
    if len(roots) == 1:
        return "\n".join(_goal_one(roots[0], text, cfg)) + "\n"
    lines = []
    for r in roots:  # paths in the packs are relative to their repo: say which one
        lines += [f"# === repo {os.path.basename(r)} ({'root' if r == repo else 'nested, paths relative to it'}) ===", *_goal_one(r, text, cfg), ""]
    return "\n".join(lines) + "\n"


def _repo_header(root, repo):
    return f"### repo {os.path.basename(root)}" + ("" if root == repo else " (nested: paths relative to it)")


def _explore_digest(repo, roots, text):
    """Lines from the explorer, or None when it gave no answer."""
    from localmodel import explore as ex

    got = ex.explore({os.path.basename(r.rstrip("/")): r for r in roots}, text)
    if got is None:
        return None
    lines = []
    for root in roots:
        mine = [(p, w, c) for r, p, w, c in got if r == os.path.basename(root.rstrip("/"))]
        if mine:
            lines += [_repo_header(root, repo), *_explore_one(root, [m[0] for m in mine], mine)]
    return lines or ["(the local model found no files)"]


def _model_digest(repo, roots, text, cfg):
    lines = []
    for root in roots:
        try:
            named = model_files(root, text, cfg)
        except Exception as e:  # Strata or index trouble never fails the planner
            lines.append(f"(local model down: {str(e)[:80]})")
            continue
        got = digest(root, with_tests(root, with_docs(root, named)))
        if got and len(roots) > 1:
            lines.append(_repo_header(root, repo))
        lines += got
    return lines or ["(local model down: no files named)"]


def _phase_digest(repo, text, cfg):
    roots = pick_roots(repo, text, cfg)
    if cfg.get("mode", "explore") == "explore":
        t = time.time()
        got = _explore_digest(repo, roots, text)
        print(
            f"plan_context: explore {time.time() - t:.0f}s, {'no answer: falling back to the section digest' if got is None else 'answered'}",
            file=sys.stderr,
            flush=True,
        )
        if got is not None:
            return got
    t = time.time()
    lines = _model_digest(repo, roots, text, cfg)
    print(f"plan_context: section digest {time.time() - t:.0f}s", file=sys.stderr, flush=True)
    return lines


PHASE_CACHE = "plan-context.cache.json"  # in the phase folder: the key and the text of the last build
DEGRADED_MARKERS = ("(local model down",)  # lines _phase_digest writes when the model failed: such a build is never cached


def _phase_key(repo, phase):
    """-> the cache key: the phase row (goal, paths, done_when) hashed, and git HEAD. A new goal or a new commit makes a new key."""
    head = gitx.run(repo, "rev-parse", "HEAD").stdout.strip()
    return {"goal": hashlib.sha256(json.dumps(phase, sort_keys=True).encode()).hexdigest(), "head": head}


def phase_context(repo, feature_dir, phase, cfg=None):
    """pipeline.phase_context.phase_context with the local-model file digest. Cached by (goal hash, git HEAD): a retry or an escalation of the same phase reuses the text."""
    cache = os.path.join(feature_dir, "phases", phase["dir"], PHASE_CACHE)
    key = _phase_key(repo, phase)
    try:
        with open(cache, encoding="utf-8") as fh:
            saved = json.load(fh)
        if saved.get("key") == key and isinstance(saved.get("text"), str):
            return saved["text"]
    except (OSError, ValueError, AttributeError):
        pass  # no cache or a damaged one: build again
    text = _phase.phase_context(repo, feature_dir, phase, cfg, lambda *a: _phase_digest(*a))
    if any(m in text for m in DEGRADED_MARKERS):  # the local model was down: the pack is poor, so a retry must build again
        return text
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump({"key": key, "text": text}, fh)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("goal")
    g.add_argument("repo")
    g.add_argument("--text-file", required=True)
    g.add_argument("--out", required=True)
    s = sub.add_parser("stage")
    s.add_argument("repo")
    s.add_argument("--stage-dir", required=True)
    s.add_argument("--base", required=True)
    s.add_argument("--head", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--verdict", action="store_true")
    s.add_argument("--workspace", default=None, help="folder of pipeline.json (its `context.ignore` decides which modified files count for the recall)")
    ph = sub.add_parser("phase")
    ph.add_argument("repo")
    ph.add_argument("--feature", required=True)
    ph.add_argument("--phase", required=True)
    ph.add_argument("--out", default=None)
    ph.add_argument("--workspace", default=".")
    i = sub.add_parser("index")
    i.add_argument("--workspace", default=".")
    g.add_argument("--workspace", default=None, help="folder of pipeline.json (its `context` object tunes the run)")
    a = ap.parse_args(argv)
    if a.cmd == "index":
        for r, t in ensure_index(a.workspace):
            print(f"index {r}: {t} s")
        return 0
    if a.cmd == "phase":
        fdir = os.path.join(a.workspace, WORK_DIR, a.feature)
        row = next((p for p in _plan_rows(fdir) if p["dir"] == a.phase), None)
        if row is None:
            print(f"plan_context phase: no phase {a.phase} in {fdir}/Plan.md")
            return 2
        body = phase_context(a.repo, fdir, row, ctx_config(a.workspace))
        a.out = a.out or os.path.join(fdir, "phases", a.phase, "plan-context.md")
    elif a.cmd == "goal":
        with open(a.text_file, encoding="utf-8", errors="replace") as fh:
            goal_text = fh.read()
        body = goal_context(a.repo, goal_text, ctx_config(a.workspace) if a.workspace else None, a.out + ".explore.log")
    else:
        body = stage_context(a.repo, a.stage_dir, a.base, a.head, ctx_config(a.workspace) if a.workspace else None)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    Path(a.out).write_text(body, encoding="utf-8")
    print(("VERDICT: DONE\n" if getattr(a, "verdict", False) else "") + f"plan_context {a.cmd}: {len(body.splitlines())} lines -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
