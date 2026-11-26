"""plan.py check, per-task part: the lint of one task file, its Predicted paths, Test lines, anchors and refs. pipeline.plan_check re-exports these names."""

import fnmatch
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import gitx
from .cite_check import _cite_msg, _resolve_bare, cites
from .configuration import (
    CITE_BACKTICK_FILE_RE,
    SCOPE_TEST_RE,
    TASKS_FILE,
    WORK_DIR,
    project,
    repo_dir,
)
from .plan_commands import command_findings
from .plan_files import (
    PATH_DIRS,
    PATH_NAME,
    paths_in,
)
from .plan_rules import (
    _transitive_deps,
    nested_repo,
)
from .shellwords import split_commands
from .stagedir import workspace_of
from .util import TOOLS, read_text
from .worktree import nested_repos


def _lint(text, repo=None, git=None):
    sys.path.insert(0, TOOLS)
    import brieflint

    return brieflint.lint(text, repo, git)


ANCHOR_RE = re.compile(rf"(?<![\w./-])({PATH_DIRS}+{PATH_NAME}\.\w+):(\d+)")


def _workspace(d):
    """The dir holding .work (where pipeline.json lives)."""
    return workspace_of(d)


def _work_root(d):
    """The main-tree `.work` dir of the workspace that holds task file dir D."""
    return os.path.join(_workspace(d), WORK_DIR)


def _in_work_root(d, p):
    """True when path P is absolute and lies under the workspace `.work` dir (scratch output, outside git)."""
    if not os.path.isabs(p):
        return False
    root = os.path.normpath(_work_root(d))
    full = os.path.normpath(p)
    return full == root or full.startswith(root + os.sep)


def repo_of(d):
    """Public name of _repo_root (plan.py dryrun)."""
    return _repo_root(d)


def _repo_root(d):
    """Repo the stage works in: pipeline.json `repo` next to .work, else the dir holding .work."""
    top = _workspace(d)
    return repo_dir(top, project(top))


TEST_FILE_RE = re.compile(SCOPE_TEST_RE)
SYMBOL_RE = re.compile(r"`([A-Za-z_][\w]{3,})`")
SKIP_DIRS = {".git", "node_modules", "target", WORK_DIR, "dist", "__pycache__"}


def _task_symbols(text):
    """The `symbols` named on the Goal:/Done: lines of a task file."""
    return {m for line in text.splitlines() if line.startswith(("# ", "Goal:", "Done:")) for m in SYMBOL_RE.findall(line)}


def _unplanned_test_files(repo, predicted, text):
    """Yield (path, rel) of each test file under REPO outside the Predicted paths and not named in the task text."""
    for root, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo)
            if not TEST_FILE_RE.search(rel) or any(rel == p or rel.startswith(p + "/") for p in predicted) or rel in text:
                continue  # `rel in text`: the brief names the test file (e.g. "stays unchanged because ..."), so the planner already decided
            yield os.path.join(root, f), rel


def check_test_refs(d, tid, repo):
    """Problems for one task file: a test file outside Predicted paths that mentions a `symbol` of the task's Goal/Done, so the dev may have to update it and would stop (BLOCKED)."""
    text = read_text(os.path.join(d, tid + ".md"))
    predicted = [p.rstrip("/") for p in paths_in(text)]
    syms = _task_symbols(text)
    if not syms or not predicted:
        return []
    rx = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(syms))) + r")\b")
    probs = []
    for path, rel in _unplanned_test_files(repo, predicted, text):
        try:
            hit = rx.search(Path(path).read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if hit:
            probs.append(f"{tid}: test {rel} mentions `{hit[0]}` but is not in Predicted paths (add it, or say why it stays)")
    return probs[:5]


def check_test_globs(d, tid, repo):
    """Problems for one task file: a `Test:` path or glob that matches no file in the repo and that no Predicted path would create (the run is empty, qrun exits 3 and the dev stops BLOCKED)."""
    text = read_text(os.path.join(d, tid + ".md"))
    m = re.search(r"(?m)^Test:\s*(.*)$", text)
    if not m:
        return []
    made = paths_in(text)
    from .tasks import parse_tasks  # local: keeps the import graph as it was

    deps = {t["id"]: t.get("depends", []) for t in parse_tasks(os.path.join(d, TASKS_FILE))}
    todo, seen = list(deps.get(tid, [])), set()
    while todo:  # a path that a task this one depends on creates counts as created (transitive)
        dep = todo.pop()
        if dep in seen:
            continue
        seen.add(dep)
        made = made + paths_in(read_text(os.path.join(d, dep + ".md")))
        todo += deps.get(dep, [])
    probs = []
    for tok in m[1].replace("`", " ").split():
        if tok.startswith(("-", "/")) or "/" not in tok or not re.search(r"[*?]|\.\w+$", tok):
            continue  # an option, an absolute tool path, a bare word or a directory
        if not glob.glob(os.path.join(repo, tok)) and not any(fnmatch.fnmatch(x, tok) for x in made):
            probs.append(f"{tid}: Test path {tok} matches no file and no Predicted path creates one (check the extension and directory)")
    return probs


CREATE_RE = re.compile(r"\b(?:add|new|create)\s+(?:(?:a|an|the)\s+)?(`[^`]+`|[\w./-]+)", re.IGNORECASE)


def _created_names(text):
    """Names the Goal:/Done: lines ask to add: a backticked word, or a path-like word (with `/` or an extension)."""
    names = set()
    for line in text.splitlines():
        if not line.startswith(("Goal:", "Done:")):
            continue
        for m in CREATE_RE.finditer(line):
            raw = m.group(1)
            name = raw.strip("`")
            if raw.startswith("`") or "/" in name or re.search(r"\.\w+$", name):
                names.add(name)
    return names


def _exists_hit(repo, predicted, name):
    """`file:line` where NAME already exists: a file at that repo path, or a def/class of NAME in a Predicted path."""
    if "/" in name or re.search(r"\.\w+$", name):
        return f"{name}:1" if os.path.isfile(os.path.join(repo, name)) else None
    rx = re.compile(rf"^\s*(?:def|class)\s+{re.escape(name)}\b", re.MULTILINE)
    for p in predicted:
        full = os.path.join(repo, p)
        if not os.path.isfile(full):
            continue
        if shutil.which("lp"):  # the code index is the first choice when it is installed
            r = subprocess.run(["lp", "nav", "def", p, name], cwd=repo, capture_output=True, text=True, check=False, timeout=30)
            hit = re.search(r"^(\S+?):(\d+)", r.stdout, re.MULTILINE)
            if r.returncode == 0 and hit:
                return f"{hit[1]}:{hit[2]}"
        try:
            text = Path(full).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = rx.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            return f"{p}:{line}"
    return None


def check_exists(d, tid, repo):
    """Problems for one task file: a name its Goal/Done asks to add (file, def or class) that already exists, so the dev would redo finished work."""
    text = read_text(os.path.join(d, tid + ".md"))
    predicted = [p.rstrip("/") for p in paths_in(text)]
    probs = []
    for name in sorted(_created_names(text)):
        hit = _exists_hit(repo, predicted, name)
        if hit:
            probs.append(f"{tid}: `{name}` already exists at {hit} (the Goal asks to add it; change the task or name a new one)")
    return probs


CITE_LINE_PREFIXES = ("Goal:", "Done:", "Pattern:")  # the task lines whose cites must exist (or be predicted by this task or a task it depends on)


def _in_tree(repo, path):
    """True when path is a file of the tree (a bare file name is looked up in the tree as cite_check does)."""
    return os.path.isfile(os.path.join(repo, _resolve_bare(repo, path)))


def check_cite_exists(d, tid, repo, ents):
    """Problems for one task file: a `path:line` cite or backticked file in its Goal/Done/Pattern lines whose file is not in the tree and is not a Predicted path of this task or of a task it depends on (an earlier wave); a cite of an existing file must hold the backticked symbol before it (cite_check._cite_msg)."""
    text = read_text(os.path.join(d, tid + ".md"))
    allowed = set(paths_in(text))
    for dep in _transitive_deps(ents, tid):
        allowed |= set(paths_in(read_text(os.path.join(d, dep + ".md"))))
    probs = []
    for ln in text.splitlines():
        if not ln.startswith(CITE_LINE_PREFIXES):
            continue
        found = list(cites(ln))
        found += [(m[1], None, None, "") for m in CITE_BACKTICK_FILE_RE.finditer(ln)]
        for path, line, end, before in found:
            if _in_tree(repo, path):
                if line is None:
                    continue
                msg = _cite_msg(repo, f"{tid} cites {path}:{line}", path, line, end, before)
                if msg:
                    probs.append(f"{tid}: {msg}")
            elif path not in allowed:
                probs.append(f"{tid}: cite-missing: {path} is not in the tree and no task of this task's dependencies lists it in Predicted paths: {ln[:120]}")
    return probs


def check_test_commands(d, tid, repo, produced):
    """Problems and `cmd:` warnings for one task file's `Test:` command: its executable and path arguments must exist (in the repo or the nested repo the task edits) or be made by the plan. See plan_commands.command_findings."""
    text = read_text(os.path.join(d, tid + ".md"))
    m = re.search(r"(?m)^Test:\s*(.*)$", text)
    if not m:
        return []
    repos = nested_repos(repo)
    nested = sorted({os.path.join(repo, n) for n in (nested_repo(repo, p, repos) for p in paths_in(text)) if n})
    return command_findings(f"{tid} Test", m[1].replace("`", " "), repo, produced, nested)


LEAD_MARK = "lead-verified"  # a Done: or Test: line with this word (outside a code span) is run by the driver after the dev, never by the dev
_CODE_SPAN = re.compile(r"`[^`]*`")
DOCS_INDEX_CMD = re.compile(r"docs_index|cs-docs-index")  # the docs index checker: a Done or Test line that runs it must be lead-verified


def is_lead_verified(line):
    """True for a `Done:` or `Test:` line whose marker word sits outside backtick code spans (a marker quoted in a code span is not a marker)."""
    return line.startswith(("Done:", "Test:")) and LEAD_MARK in _CODE_SPAN.sub("", line)


def lead_verified_lines(text):
    """The `Done:` and `Test:` lines of a task file marked lead-verified."""
    return [ln for ln in text.splitlines() if is_lead_verified(ln)]


def check_lead_verified(d, tid):
    """Problems for one task file: a `Done:` or `Test:` line that runs the docs index checker without the lead-verified marker (the lead runs that check, never the dev)."""
    return [
        f"{tid}: a Done or Test line runs the docs index checker without the lead-verified marker: {ln[:120]}"
        for ln in read_text(os.path.join(d, tid + ".md")).splitlines()
        if ln.startswith(("Done:", "Test:")) and DOCS_INDEX_CMD.search(ln) and not is_lead_verified(ln)
    ]


def check_test_quoting(d, tid):
    """Problems for one task file: a `Test:` line whose `-e`/`-c` expression is not quoted (bash parses its parentheses and the run dies before the code starts)."""
    m = re.search(r"(?m)^Test:\s*(.*)$", read_text(os.path.join(d, tid + ".md")))
    hit = re.search(r"\s-[ec]\s+([^\s\"'][^\s]*\()", m[1]) if m else None
    return [f"{tid}: Test line has an unquoted -e/-c expression `{hit[1]}` (put it in double quotes)"] if hit else []


def check_git_paths(d, tid, repo):
    """Problems for one task file: a Predicted path whose nearest existing parent is in no git work tree (the dev cannot commit there and stops with BLOCKED "no git repo")."""
    probs = []
    for p in paths_in(read_text(os.path.join(d, tid + ".md"))):
        if _in_work_root(d, p):
            continue  # the stage's scratch output under <workspace>/.work: never in git, no commit needed
        anc = os.path.normpath(os.path.join(repo, p))
        while not os.path.isdir(anc) and os.path.dirname(anc) != anc:
            anc = os.path.dirname(anc)
        try:
            r = gitx.run(anc, "rev-parse", "--is-inside-work-tree")
            ok = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):  # git missing or hung: report the path, do not crash the check
            ok = False
        if not ok:
            probs.append(f"{tid}: Predicted path {p} is in no git repo (the dev cannot commit it): drop it or move that work out of the task")
    return probs


def check_denied_commands(d, tid, denied):
    """Problems for one task file: a `Test:` command that starts with one of the sandbox-denied prefixes (pipeline.json `preflight_denied`, e.g. "rustup", "chmod", "./tool build"); the dev would be denied and stop BLOCKED. [] when the list is unset."""
    m = re.search(r"(?m)^Test:\s*(.*)$", read_text(os.path.join(d, tid + ".md")))
    if not m or not denied:
        return []
    probs = []
    for part in split_commands(m[1].replace("`", " "), amp=False, newline=False):
        words = part.strip()
        hit = next((x for x in denied if words == str(x) or words.startswith(str(x) + " ")), None)
        if hit:
            probs.append(f"{tid}: Test runs `{hit}`, which the sandbox denies: move it to a script the driver runs or drop it")
    return probs


MAX_WARNINGS = 25
WARN_COUNT_RE = re.compile(r"(?i)\b(\d+)\s+(?:\w+\s+){0,2}warnings?\b")


def check_warning_count(d, tid):
    """`size:` warning (never fails a check): the Goal names more than MAX_WARNINGS warnings to fix, so the dev may run out of budget; split by file."""
    text = read_text(os.path.join(d, tid + ".md"))
    lines = text.splitlines()
    # the heading (title) stands in when there is no Goal line
    goal = next((line for line in lines if line.startswith("Goal:")), "") or next((line for line in lines if line.startswith("# ")), "")
    n = max((int(m[1]) for m in WARN_COUNT_RE.finditer(goal)), default=0)
    return [f"size: {tid}: Goal names {n} warnings (> {MAX_WARNINGS}): split the task by file or module"] if n > MAX_WARNINGS else []


PATH_FIELD_RE = re.compile(r"(?im)^(?:Predicted paths|Pattern|Test):\s*(.*)$")


def _repo_prefixed(line, name):
    """The first `name/x` (x a path character) in `line` that does not continue a longer path, else ''."""
    head = name + "/"
    i = line.find(head)
    while i != -1:
        before = line[i - 1] if i else ""
        after = line[i + len(head) : i + len(head) + 1]
        if not (before.isalnum() or before in ("_", ".", "/", "-")) and (after.isalnum() or after in ("_", ".", "-")) and after:
            return head + after
        i = line.find(head, i + 1)
    return ""


def check_repo_prefix(d, tid, repo):
    """Problems for one task file: a path in Predicted paths / Pattern / Test starting with the repo directory name (`myrepo/...`). The dev runs in a worktree rooted at the repo, so such a path does not exist there."""
    name = os.path.basename(os.path.normpath(repo))
    if not name or os.path.normpath(repo) == os.path.normpath(_workspace(d)):
        return []
    text = read_text(os.path.join(d, tid + ".md"))
    hits = [h for line in PATH_FIELD_RE.findall(text) for h in [_repo_prefixed(line, name)] if h]
    return [f"{tid}: path starts with `{name}/` ({hits[0]}...): the dev runs in the {name} root, use repo-relative paths without that prefix"] if hits else []


WORK_OUTPUT_RE = re.compile(r"(?im)^(?:Predicted paths|Done):\s*(.*)$")
RELATIVE_WORK_RE = re.compile(r"(?<![\w./-])\.work/[\w./-]+")


def check_work_paths(d, tid):
    """Problems for one task file: a relative `.work/` output path in Predicted paths / Done. A scout or dev task runs in a job worktree that is deleted afterwards, so such an output is lost; the absolute main-tree path is required."""
    text = read_text(os.path.join(d, tid + ".md"))
    hits = [m[0] for line in WORK_OUTPUT_RE.findall(text) for m in [RELATIVE_WORK_RE.search(line)] if m]
    if not hits:
        return []
    stage = os.path.basename(os.path.normpath(d))
    main = os.path.join(_work_root(d), stage)
    return [f"{tid}: output `{hits[0]}` is a relative .work path: the task worktree is deleted afterwards, so use the absolute main-tree path {main}/..."]


def _suffix_matches(repo, short):
    """-> the tracked files of REPO that end with `/short`; [] when REPO is no git work tree."""
    try:
        r = gitx.run(repo, "ls-files", "-z")
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode:
        return []
    return [f for f in r.stdout.split("\0") if f.endswith("/" + short)]


def _resolve_short(repo, f, tid, line):
    """-> the repo-relative path for a short anchor path F when exactly one tracked file ends with it (prints a `warn:` naming the full path), else ''."""
    hits = _suffix_matches(repo, f)
    if len(hits) != 1:
        return ""
    print(f"warn: {tid}: anchor {f}:{line} is not repo-relative; use {hits[0]}:{line}", file=sys.stderr)
    return hits[0]


def check_refs(d, tid, repo):
    """Problems for one task file: an anchor `path/file.ext:N` whose file is missing or shorter than N lines (stale since the plan was written). New files in Predicted paths are fine."""
    text = read_text(os.path.join(d, tid + ".md"))
    probs = []
    for f, line in sorted(set(ANCHOR_RE.findall(text))):
        full = os.path.join(repo, f)
        if not os.path.isfile(full):
            if any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/") for p in paths_in(text)):
                continue  # the task creates it
            short = _resolve_short(repo, f, tid, line)
            if not short:
                probs.append(f"{tid}: anchor {f}:{line}: file not found in {repo}")
                continue
            full = os.path.join(repo, short)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                n = sum(1 for _ in fh)
        except OSError:
            continue
        if int(line) > n:
            probs.append(f"{tid}: anchor {f}:{line}: file has only {n} lines")
    return probs
