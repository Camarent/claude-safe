"""stage_recall: plan vs changes of a stage, and how much of the changed code the planner context listed. No model."""

import fnmatch
import os
import re
import time
from collections import defaultdict

from search.ctx_config import CTX_DEFAULTS, pick_roots
from search.lpcall import changed_files, git_text

from . import gitx
from .configuration import METRICS_LOG
from .plan_files import paths_in
from .util import swallowed


def task_info(path) -> tuple[str, list[str], str]:
    with open(path, errors="replace") as fh:
        t = fh.read()

    def g(pat) -> str:
        m = re.search(pat, t, re.M)
        return m[1].strip() if m else ""

    return g(r"^# (.*)$") or os.path.basename(path), paths_in(t), g(r"^Done:\s*(.*)$")


def covered(f, paths) -> bool:
    return any(fnmatch.fnmatch(f, p) or f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/") for p in paths)


def pack_paths(files, limit=8) -> list[str]:
    """['- a', '- b'] for a short list; one `dir/ (n files)` line per directory when it is long."""
    if len(files) <= limit:
        return ["- " + f for f in files]
    by: defaultdict = defaultdict(list)
    for f in files:
        by[os.path.dirname(f) or "."].append(os.path.basename(f))
    return [f"- {d}/ ({len(v)}): " + ", ".join(v[:4]) + (f" +{len(v) - 4}" if len(v) > 4 else "") for d, v in sorted(by.items())]


def context_paths(ctx_text) -> list[str]:
    """Every file path the planner context lists in its `###` groups (tests are stored as `dir/: a, b`)."""
    out, group = [], None
    for line in ctx_text.splitlines():
        if line.startswith("## "):
            group = None
        elif line.startswith("### "):
            group = line[4:].split()[0] if line[4:].split() else None
        elif group and line.startswith("- "):
            if group == "tests":  # `- dir/: a, b` (names of a directory) or `- path: why` (one file, the why after the colon)
                head, sep, names = line[2:].partition(": ")
                if sep and head.endswith("/"):
                    out += [head.rstrip("/") + "/" + n.strip() for n in names.split(",") if n.strip()]
                else:
                    out.append(head.strip())
            else:
                out.append(line[2:].split(" - ")[0].strip())
    return out


def modified_files(repo, base, head) -> list[str]:
    """Files that existed at BASE and differ at HEAD (added, deleted and renamed files are not counted: no context could have named a new file)."""
    return [
        x for x in git_text(["diff", "-z", "--name-only", "--diff-filter=M", f"{base}..{head}"], repo, 30).split("\0") if x
    ]  # -z: names verbatim, NUL-separated


def live_recall(repo, base, head, ctx_text, cfg=None) -> tuple[int, int, list[str]]:
    """(hits, total, missed): of the files the work modified, how many the planner context listed. Needs no reference list.
    Files the naming step ignores by config (audio packs, caches, ...) are left out of both counts: it never could have named them."""
    ign = (cfg or CTX_DEFAULTS)["ignore"]
    mod = [m for m in modified_files(repo, base, head) if not m.startswith(".code-index/") and not any(fnmatch.fnmatch(m, g) for g in ign)]
    have = set(context_paths(ctx_text))
    return sum(1 for m in mod if m in have), len(mod), [m for m in mod if m not in have]


def pack_sections(ctx_text, repo) -> dict[str, str]:
    """{repo dir: its part of a multi-repo pack}; a single-repo pack is all REPO's. A feature pack marks a repo `# === repo X (`, a phase pack `### repo X`."""
    parts = re.split(r"^(?:# === |### )repo (\S+)(?= \(|$)", ctx_text, flags=re.M)
    if len(parts) == 1:
        return {repo: ctx_text}
    out = {repo: parts[0]}
    for name, body in zip(parts[1::2], parts[2::2], strict=False):
        d = next((r for r in pick_roots(repo, "", CTX_DEFAULTS) if os.path.basename(r) == name), None)
        out[d or os.path.join(repo, name)] = out.get(d or "", "") + body
    return out


def merge_packs(packs, repo) -> dict[str, str]:
    """pack_sections over several packs (CTX_TEXT may be one text or a list): the sections of the same repo are joined."""
    out: dict = {}
    for text in [packs] if isinstance(packs, str) else packs:
        for d, body in pack_sections(text, repo).items():
            out[d] = out[d] + "\n## \n" + body if d in out else body
    return out


def _has_branches(r, base, head):
    return all(gitx.run(r, "rev-parse", "-q", "--verify", b).returncode == 0 for b in (base, head))


def all_recall(repo, base, head, ctx_text, cfg) -> tuple[int, int, list[str]]:
    """live_recall summed over the root and every indexed repo that has both branches; missed paths carry the repo name when it is not the root."""
    hits = total = 0
    missed = []
    secs = merge_packs(ctx_text, repo)
    for r in pick_roots(repo, "", cfg):
        if not _has_branches(r, base, head):
            continue
        h, t, m = live_recall(r, base, head, secs.get(r, ""), cfg)
        hits, total = hits + h, total + t
        missed += [m_ if r == repo else f"{os.path.basename(r)}:{m_}" for m_ in m]
    return hits, total, missed


def named_count(repo, base, head, ctx_text, cfg) -> int:
    """How many distinct files the context lists, in the repos all_recall measures (those with both branches)."""
    secs = merge_packs(ctx_text, repo)
    return sum(len(set(context_paths(secs.get(r, "")))) for r in pick_roots(repo, "", cfg) if _has_branches(r, base, head))


def find_context_file(stage_dir) -> str:
    """plan-context.md of a stage or of the feature a phase folder (`.work/F/phases/NN-x`) belongs to; "" when there is none."""
    for rel in ("plan-context.md", os.path.join("..", "..", "plan-context.md")):
        f = os.path.normpath(os.path.join(stage_dir, rel))
        if os.path.isfile(f):
            return f
    return ""


def find_context_files(stage_dir) -> list[str]:
    """The context files a stage's recall is measured against: its own plan-context.md, else the feature's. A phase with no pack of its own (the finish phase audits the whole feature) adds the packs of all sibling phases."""
    own = os.path.join(stage_dir, "plan-context.md")
    cf = find_context_file(stage_dir)
    if not cf or cf == own:
        return [cf] if cf else []
    sib = os.path.normpath(os.path.join(stage_dir, ".."))
    packs = [os.path.join(sib, d, "plan-context.md") for d in sorted(os.listdir(sib))] if os.path.isdir(sib) else []
    return [cf, *(p for p in packs if os.path.isfile(p))]


def _stage_tasks(stage_dir):
    """[(file, title, predicted paths, done line)] of the stage's task-N.md files; [] when the folder is missing."""
    if not os.path.isdir(stage_dir):
        return []
    return [
        (f, *task_info(os.path.join(stage_dir, f)))
        for f in sorted(os.listdir(stage_dir))
        if f.startswith("task-") and f.endswith(".md") and f[5:-3].isdecimal()
    ]


def _log_recall(stage_dir, hits, total, missed, named=0, mode=""):
    try:
        from pipeline.util import append_jsonl

        append_jsonl(
            os.path.join(stage_dir, METRICS_LOG),
            {
                "ev": "context-recall",
                "ts": time.time(),
                "stage": os.path.basename(stage_dir.rstrip("/")),
                "hits": hits,
                "total": total,
                "missed": missed[:200],
                "named": named,
                "precision": round(hits / named, 3) if named else 0,
                "mode": mode,
                "local_model": os.environ.get("STRATA") != "0",
            },
        )
    except Exception as ex:  # fail open on purpose
        swallowed("plan_context.recall", ex)


def _recall_lines(repo, stage_dir, base, head, cfg, cfs):
    """The planner-context recall section for the context files CFS; also appends the measurement to the stage's metrics.jsonl."""
    texts = []
    for cf in cfs:
        with open(cf, encoding="utf-8", errors="replace") as fh:
            texts.append(fh.read())
    hits, total, missed = all_recall(repo, base, head, texts, cfg or CTX_DEFAULTS)
    named = named_count(repo, base, head, texts, cfg or CTX_DEFAULTS)
    mode = "explore" if any("local model that explored" in t for t in texts) else "batch"
    lines = [
        "",
        f"## Planner context recall: {hits} of {total} modified files were listed in plan-context.md ({named} files named, precision {hits / named:.0%}, mode {mode})"
        if named
        else f"## Planner context recall: {hits} of {total} modified files were listed in plan-context.md",
    ] + (["- missed: " + ", ".join(missed[:60])] if missed else [])
    _log_recall(stage_dir, hits, total, missed, named, mode)
    return lines


def _task_sections(tasks, changed):
    """One section per task: title, Predicted paths, Done line, the changed files under its paths."""
    lines = []
    for f, title, paths, done in tasks:
        lines += [
            "",
            f"## {title if title.startswith(f[:-3]) else f[:-3] + ': ' + title}",
            f"Predicted paths: {', '.join(paths) or '(none)'}",
            f"Done: {done or '(none)'}",
            "Changed under these paths: " + (", ".join(c for c in changed if covered(c, paths))[:300] or "nothing"),
        ]
    return lines


def stage_context(repo, stage_dir, base, head, cfg=None) -> str:
    changed = changed_files(repo, base, head)
    tasks = _stage_tasks(stage_dir)
    lines = [
        f"# Plan vs changes ({base}..{head}), by script",
        "",
        f"{len(tasks)} planned tasks, {len(changed)} changed files. Paths are matched as globs, prefixes or exact names.",
    ]
    allp = [p for _, _, paths, _ in tasks for p in paths]
    lines += _task_sections(tasks, changed)
    outside = [c for c in changed if not covered(c, allp)]
    unused = [p for f, _, ps, _ in tasks for p in ps if not any(covered(c, [p]) for c in changed)]
    lines += ["", "## Changed but not in any task's Predicted paths"] + (pack_paths(outside) or ["(none)"])
    lines += ["", "## Predicted paths with no change"] + (["- " + p for p in unused[:40]] or ["(none)"])
    cfs = find_context_files(stage_dir)
    if cfs:
        lines += _recall_lines(repo, stage_dir, base, head, cfg, cfs)
    return "\n".join(lines) + "\n"
