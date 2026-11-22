"""phase_context: the context file for the architect of phase 2..N: goal, plan-notes decisions, merged work, earlier phases' digest (tasks, deviations, reports, audits, DOCS-STALE). The file digest comes from DIGEST (the local-model part lives in localmodel/plan_context)."""

import glob
import os
import re
import sys
import time
from pathlib import Path

from search.ctx_config import CTX_DEFAULTS, ensure_index, pick_roots
from search.lpcall import git_text

from pipeline import plan_files as pf
from pipeline.configuration import (
    AUDITS_DIR,
    BASE_FILE,
    DEVIATION_TAG,
    DIGEST_CHARS_CAP,
    DIGEST_LINES_CAP,
    DIGEST_STAT_CAP,
    DIGEST_TASKS_CAP,
    DOCS_STALE_TAG,
    FAIL,
    FIXES,
    PHASES_DIR,
    PLAN_FILE,
    REPORTS_DIR,
    TASKS_FILE,
    project,
    repo_dirs,
)
from pipeline.util import swallowed

NOTES_CAP = 8000  # chars of plan-notes.md kept in a phase context (the real notes are about 5000)
BRIEF_DIGEST_BYTES = 1024  # bytes of request.md + plan-context.md a fix architect gets (brief_digest)
STAT_CAP = DIGEST_STAT_CAP  # lines of `git diff --stat` kept per repo in a phase context (kept name for callers)
VERDICT_RE = re.compile(r"VERDICT:\s*(\w+)")  # first line of a report or audit file
AUDIT_VERSION_RE = re.compile(r"(-\d+|-opus)?\.md$")  # `audit-sec-2.md`, `audit-sec-opus.md` are rounds of `audit-sec.md`
TAGGED_LINE_STARTS = (DEVIATION_TAG, DOCS_STALE_TAG)


def _notes_lines(text, phase):
    """plan-notes.md whole when small, else its decision sections plus the lines that name the phase or one of its paths (hard cap NOTES_CAP chars)."""
    if len(text) <= NOTES_CAP:
        return text.strip()
    name = phase["dir"].split("-", 1)[-1]
    keys = [k for k in [phase["dir"], name, *(p.replace("*", "").rstrip("/") for p in phase["paths"])] if len(k) > 2]
    hit, in_decisions = [], False
    for x in text.splitlines():
        if x.startswith("#"):
            in_decisions = "decision" in x.lower()
        if in_decisions or any(k in x for k in keys):
            hit.append(x)
    return ("\n".join(hit) or text)[:NOTES_CAP]


def brief_digest(request, context, cap=BRIEF_DIGEST_BYTES):
    """-> at most CAP UTF-8 bytes for a fix architect's brief: the head of request.md, then the head of plan-context.md with what room is left."""
    head = "## request (head)\n" + request.strip() + "\n\n## plan context (head)\n"
    return (head + context.strip()).encode("utf-8")[:cap].decode("utf-8", errors="ignore")


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _plan_rows(fdir):
    return pf.parse_phase_rows(_read(os.path.join(fdir, PLAN_FILE)))


def earlier_rows(fdir, phase):
    """Plan.md rows before PHASE, in order."""
    rows = _plan_rows(fdir)
    return rows[: next((i for i, r in enumerate(rows) if r["dir"] == phase["dir"]), 0)]


def _verdict(path):
    m = VERDICT_RE.match(_read(path).split("\n", 1)[0].strip())
    return m[1] if m else ""


def _md_files(folder):
    return sorted(glob.glob(os.path.join(folder, "*.md"))) if os.path.isdir(folder) else []


def _tagged(path):
    """Lines of a report or audit that the next phase must read: a FAIL first line, and every DEVIATION or DOCS-STALE line."""
    name = os.path.basename(path)
    out = [f"{name}: VERDICT: {FAIL}"] if _verdict(path) == FAIL else []
    out += [f"{name}: {x.strip()}" for x in _read(path).splitlines() if x.startswith(TAGGED_LINE_STARTS)]
    return out


def _open_fixes(audit_files):
    """Audit files whose last round still says FIXES: per audit (name without its round suffix) only the newest file counts."""
    latest = {}
    for f in sorted(audit_files, key=lambda x: (len(os.path.basename(x)), os.path.basename(x))):  # shorter name first: `audit-sec.md`, then `-2`, then `-opus`
        latest[AUDIT_VERSION_RE.sub(".md", os.path.basename(f))] = f
    return [f for f in latest.values() if _verdict(f) == FIXES]


def earlier_open_items(fdir, phase, workspace):
    """Open items of the phases before PHASE: FAIL reports, DEVIATION and DOCS-STALE lines, audits whose last round says FIXES (one line each, not capped; the digest caps them)."""
    out = []
    for r in earlier_rows(fdir, phase):
        folder = os.path.join(fdir, PHASES_DIR, r["dir"])
        out += [f"{r['dir']} {x}" for x in (y for f in _md_files(os.path.join(folder, REPORTS_DIR)) for y in _tagged(f))]
        audits = _md_files(os.path.join(folder, AUDITS_DIR))
        out += [f"{r['dir']} {os.path.basename(f)}: VERDICT: {FIXES}" for f in _open_fixes(audits)]
        out += [f"{r['dir']} {os.path.basename(f)}: {x.strip()}" for f in audits for x in _read(f).splitlines() if x.startswith(TAGGED_LINE_STARTS)]
    return out


def _task_lines(fdir, phase):
    """`- <phase> task-N: title [done|open]` for every task of the earlier phases (a ticked old-style box is the final state; plain lines are open)."""
    out = []
    for r in earlier_rows(fdir, phase):
        folder = os.path.join(fdir, PHASES_DIR, r["dir"])
        for line in _read(os.path.join(folder, TASKS_FILE)).splitlines():
            m = pf.BULLET.match(line)
            if m:
                done = re.match(r"- \[[xX]\]", line) is not None
                out.append(f"- {r['dir']} {m[1]}: {m[2]} [{'done' if done else 'open'}]")
    return out


def _phase_span(fdir, rows, i, head):
    """(base, end) git refs of earlier phase rows[i]: its base.txt and the next phase's base.txt (or the work branch head); None when its base.txt is missing."""
    base = _read(os.path.join(fdir, PHASES_DIR, rows[i]["dir"], BASE_FILE)).strip()
    if not base:
        return None
    nxt = _read(os.path.join(fdir, PHASES_DIR, rows[i + 1]["dir"], BASE_FILE)).strip() if i + 1 < len(rows) else ""
    return base, nxt or head


def _predicted(folder):
    """Paths the tasks of one phase said they would touch (repo-root relative)."""
    return sorted({p for e in pf.read_entries(folder) for p in pf.task_paths(folder, e["id"])})


def _under(path, folders):
    """True when PATH is one of FOLDERS or sits below one of them."""
    return any(path == f or path.startswith(f.rstrip("/") + "/") for f in folders)


def _deviation_lines(fdir, rows, head, workspace):
    """Per earlier phase: changed files outside the predicted paths, and predicted paths nothing changed. Changes come from every repo of the project."""
    out = []
    repos = repo_dirs(workspace, project(workspace))
    for i, r in enumerate(rows):
        span = _phase_span(fdir, rows, i, head)
        folder = os.path.join(fdir, PHASES_DIR, r["dir"])
        if span is None:
            out.append(f"(no {BASE_FILE} for {r['dir']}: deviations not checked)")
            continue
        predicted = _predicted(folder)
        changed = [
            (os.path.basename(repo), p)
            for repo in repos
            for p in git_text(["diff", "--name-only", f"{span[0]}..{span[1]}"], repo, 30).splitlines()
            if p.strip()
        ]
        if not predicted:
            continue
        off = [f"{name}/{p}" for name, p in changed if not _under(p, predicted)]
        unused = [p for p in predicted if not any(_under(p2, [p]) for _, p2 in changed)]
        if off:
            out.append(f"{DEVIATION_TAG} {r['dir']}: changed outside predicted paths: {', '.join(off[:DIGEST_LINES_CAP])}")
        if unused:
            out.append(f"{DEVIATION_TAG} {r['dir']}: predicted, not changed: {', '.join(unused[:DIGEST_LINES_CAP])}")
    return out


def _stat_lines(workspace, base, head):
    """`git diff --stat base..head` of every repo of the project, each capped at DIGEST_STAT_CAP lines."""
    out = []
    for repo in repo_dirs(workspace, project(workspace)):
        stat = [x for x in git_text(["diff", "--stat", f"{base}..{head}"], repo, 30).splitlines() if x.strip()]
        out += [f"### {os.path.basename(repo)}: git diff --stat {base}..{head}", *(stat[:DIGEST_STAT_CAP] or ["(nothing merged yet)"])]
    return out


def _part(label, build):
    """Lines of one digest part; a failure is logged to stderr and leaves a note line in the context (never silent, never raised)."""
    try:
        return build()
    except Exception as ex:
        swallowed(f"phase_context.{label}", ex)
        return [f"(digest part failed: {label}: {type(ex).__name__}: {str(ex)[:200]})"]


def phase_digest(fdir, phase, workspace):
    """The digest of the phases before PHASE: final task states, deviations, open items (capped), merged work per repo (capped)."""
    rows = earlier_rows(fdir, phase)
    base, head = _phase_base_head(workspace, os.path.basename(os.path.abspath(fdir)))
    tasks = _part("tasks", lambda: _task_lines(fdir, phase))
    devs = _part("deviations", lambda: _deviation_lines(fdir, rows, head, workspace))
    items = _part("open items", lambda: earlier_open_items(fdir, phase, workspace))
    stat = _part("merged", lambda: _stat_lines(workspace, base, head))
    lines = ["## earlier phases: tasks (final state from TASKS.md)", *(tasks[:DIGEST_TASKS_CAP] or ["(no earlier tasks)"])]
    if len(tasks) > DIGEST_TASKS_CAP:
        lines.append(f"(+{len(tasks) - DIGEST_TASKS_CAP} more tasks, see the earlier TASKS.md files)")
    lines += ["", "## deviations: predicted paths (TASKS.md) vs changed files per phase", *(devs or ["(no deviations)"])]
    lines += ["", "## open items: FAIL reports, DEVIATION, DOCS-STALE, audits still FIXES", *(items[:DIGEST_LINES_CAP] or ["(nothing open)"])]
    if len(items) > DIGEST_LINES_CAP:
        lines.append(f"(+{len(items) - DIGEST_LINES_CAP} more open items, see the reports and audits folders of the earlier phases)")
    lines += ["", f"## merged by earlier phases (git diff --stat {base}..{head}, every repo)", *stat]
    return lines


def _phase_base_head(workspace, feature=""):
    from pipeline.configuration import project, work_branch

    cfg = project(workspace)
    return cfg["branch_release"], work_branch(cfg, feature)


def phase_context(repo, feature_dir, phase, cfg=None, digest=None):
    """Context file text for the architect that plans PHASE (a Plan.md row dict): goal, plan-notes decisions, earlier phases' digest, file digest. DIGEST(repo, text, cfg) -> lines is the model part (none: a one-line note). Never raises on Strata or index failure; failures are logged to stderr."""
    cfg = cfg or CTX_DEFAULTS
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(feature_dir)))
    t = time.time()
    try:
        ensure_index(workspace, cfg)
    except Exception as ex:
        swallowed("phase_context.ensure_index", ex)
    print(f"phase_context: index refresh {time.time() - t:.0f}s", file=sys.stderr, flush=True)
    lines = [
        f"# Phase context for {phase['dir']} (by script; hints to verify, not truth)",
        "",
        f"goal: {phase['goal']}",
        f"paths: {', '.join(phase['paths']) or '(see Plan.md)'}",
        f"done when: {phase['done_when']}",
    ]
    notes = _notes_lines(_read(os.path.join(feature_dir, "plan-notes.md")), phase)
    lines += ["", "## decisions (plan-notes.md)", notes or "(none)"]
    digest_lines = "\n".join(phase_digest(feature_dir, phase, workspace))[:DIGEST_CHARS_CAP].split(
        "\n"
    )  # the digest part is capped, the rest of the context is not
    lines += ["", *digest_lines]
    text = f"{phase['goal']}\n{' '.join(phase['paths'])}"
    named = digest(repo, text, cfg) if digest else ["(no file digest: local model part not wired)"]
    lines += [
        "",
        f"## files the local model names for this phase (repos {', '.join(os.path.basename(r) for r in pick_roots(repo, text, cfg))}; exist, read the ones you need)",
        *named,
    ]
    missing = predicted_missing(phase["paths"], named, repo)
    if missing:  # the plan names these files, the digest does not: the architect must see them too
        lines += ["", "## predicted paths the file digest does not name (exist; by the plan)", *[f"- {p}" for p in missing]]
    return "\n".join(lines) + "\n"


def predicted_missing(paths, digest_lines, repo):
    """-> the Predicted paths that exist in REPO as files but that no digest line names, in plan order."""
    shown = "\n".join(digest_lines)
    return [p for p in paths if p not in shown and os.path.isfile(os.path.join(repo, p))]
