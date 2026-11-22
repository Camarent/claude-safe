"""plan.py check: one line per problem across Plan.md, TASKS.md, the task files and the Graph."""

import fnmatch
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

from . import gitx
from .ci_drift import drift
from .configuration import (
    AFFECTED_WARN_PREFIX,
    CARRIED_NOT_NEEDED,
    CARRIED_SECTION,
    FINAL_GATES_KEY,
    GATE_LABELS,
    GATES_KEY,
    GENERATED_WARN_PREFIX,
    MERGE_GATES_KEY,
    NEG_WARN_PREFIX,
    PLAN_FILE,
    PREREQ_WARN_PREFIX,
    PUBLISH_BUILD,
    PUBLISH_CWD,
    PUBLISH_FILES,
    PUBLISH_LINE_RE,
    PUBLISH_OUT,
    PUBLISH_PAGE,
    PUBLISH_RE,
    REQUEST_FILE,
    SCOPE_TEST_RE,
    SHARED_MIN,
    TASKS_FILE,
    TESTCMD_WARN_PREFIX,
    WEBTEST_WARN_PREFIX,
    WORK_DIR,
    ConfigError,
    project,
    publish_entries,
    registry_files_of,
    repo_dir,
    ui_paths,
)
from .plan_commands import command_findings
from .plan_depends import depends_line_problems
from .plan_files import (
    PATH_DIRS,
    PATH_NAME,
    checklist_items,
    is_later_phase,
    is_mid_phase,
    overlap,
    paths_in,
    phases_ordered,
    plan_path,
    read_entries,
    read_phases,
    started_tasks,
    task_path_modes,
    task_paths,
    tasks_path,
    wave_conflict,
)
from .plan_lint import (
    classify_shared,
    shared_file_errors,
    test_env_errors,
    ux_vars_errors,
)
from .plan_parallel import PARALLEL_WARN_PREFIX, parallel_candidates
from .plan_ptest import ptest_selector_problems
from .plan_report import check_report_validator, check_type_inputs
from .plan_rules import (
    _same,
    acceptance_problems,
    check_affected_tests,
    check_docs_target,
    check_gate_mentions,
    check_generated_paths,
    check_negative_tag,
    check_nested_repo_paths,
    check_open_edit,
    check_task_size,
    check_unlisted_prereq,
    check_webtest_type,
    done_when_problems,
    dryrun_missing_problems,
    git_state,
    manual_tag,
    nested_repo,
    per_file_problems,
    protected_gates,
    reads_as_prose,
    release_flag_problems,
)
from .plan_testcmd import check_bare_test_runner
from .shellwords import split_commands
from .stagedir import workspace_of
from .tasks import parse_flags, parse_tasks
from .util import TOOLS, read_text
from .ux_flows import covering, flows_file, flows_rel, path_problem, variables
from .ux_flows import read as read_flows
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


def _preflight_denied(d):
    v = project(_workspace(d)).get("preflight_denied")
    return v if isinstance(v, list) else []


def _task_file_problems(d, e, ents, repo, gates, git, produced):
    """Problems of one entry: its task file (lint, anchors) and its dependencies."""
    probs: list[str] = []
    f = os.path.join(d, e["id"] + ".md")
    probs += (
        [f"{e['id']}: missing {f}"]
        if not os.path.exists(f)
        else [f"{e['id']}: {m}" for m in _lint(read_text(f), repo, git)]
        + check_refs(d, e["id"], repo)
        + depends_line_problems(d, [e])
        + check_test_refs(d, e["id"], repo)
        + check_test_globs(d, e["id"], repo)
        + ptest_selector_problems(d, e["id"], repo)
        + check_test_quoting(d, e["id"])
        + check_lead_verified(d, e["id"])
        + check_test_commands(d, e["id"], repo, produced)
        + check_git_paths(d, e["id"], repo)
        + check_repo_prefix(d, e["id"], repo)
        + check_warning_count(d, e["id"])
        + check_denied_commands(d, e["id"], _preflight_denied(d))
        + check_bare_test_runner(d, e["id"], _workspace(d))
        + check_work_paths(d, e["id"])
        + check_task_size(d, e["id"])
        + check_type_inputs(d, e["id"], repo, _workspace(d))
        + check_report_validator(d, e["id"])
        + check_nested_repo_paths(d, e["id"], repo, project(_workspace(d)).get("nested_repo_dir_prefix") or "")
        + check_negative_tag(d, e["id"])
        + check_webtest_type(d, e["id"])
        + check_generated_paths(d, e["id"], repo)
        + check_affected_tests(d, e["id"], repo)
        + check_unlisted_prereq(d, e, ents)
        + check_open_edit(d, e["id"], project(_workspace(d)).get("nested_repo_dir_prefix") or "")
        + check_gate_mentions(d, e["id"], gates)
        + per_file_problems(d, e["id"])
        + dryrun_missing_problems(d, e["id"])
        + test_env_errors(d, e["id"], repo)
    )
    for dep in e["depends"]:
        dw = next((x["wave"] for x in ents if x["id"] == dep), None)
        if dw is None:
            probs.append(f"{e['id']}: depends on missing {dep}")
        elif dw >= e["wave"]:
            probs.append(f"{e['id']}: depends on {dep} (wave {dw}) but runs in wave {e['wave']} (cycle or bad order)")
    return probs


def _overlap_problems(d, ents):
    """One problem per pair of same-wave tasks whose Predicted paths conflict (plan_files.wave_conflict). Each task file is read once; only tasks of one wave are compared."""
    modes = {e["id"]: task_path_modes(d, e["id"]) for e in ents}
    waves: dict = {}
    for e in ents:
        waves.setdefault(e["wave"], []).append(e)
    probs = []
    for x in ents:
        peers = waves[x["wave"]]
        for y in peers[peers.index(x) + 1 :]:
            ov = wave_conflict(modes[x["id"]], modes[y["id"]])
            if ov:
                probs.append(
                    f"{x['id']} and {y['id']} share paths in wave {x['wave']}: {ov[0]} / {ov[1]} (same-file tasks never share a wave unless the file is .jsonl/.tsv/.csv/.ndjson and both list it as `(rows)`; a `(mass)` or unannotated entry never shares: make one depend on the other, `plan.py task --depends N`, so it runs in a later wave)"
                )
    return probs


def _listings(d, ents):
    """-> {path: [(task id, mode)]} over the Predicted paths of the stage's tasks."""
    users: dict[str, list[tuple[str, str]]] = {}
    for e in ents:
        for f, mode in dict.fromkeys(task_path_modes(d, e["id"])):
            users.setdefault(f, []).append((e["id"], mode))
    return users


def shared_warnings(d, ents):
    """`shared:` warning (never fails a check), once per file listed by SHARED_MIN or more tasks that is not a `shared-file:` error: they chain into waves; one owner task or per-feature files let them run together. Silent for a line-based file all its tasks list as `(rows)`, and for a directory or glob entry. A registry-named or data file listed by SHARED_MIN or more tasks without an owner or fragments is a `shared-file:` error (plan_lint.classify_shared)."""
    waves = {e["id"]: e["wave"] for e in ents}
    out = []
    for f, es in _listings(d, ents).items():
        if len(es) < SHARED_MIN or classify_shared(f, [m for _, m in es]) != "warn":
            continue
        ids = ", ".join(t for t, _ in es)
        out.append(
            f"shared: {f} is listed by {ids} ({len(es)} tasks, {len({waves[t] for t, _ in es})} waves): give it one owner task or per-feature files so they can share a wave; for a data file, splitting it into per-topic files as its own mass task first lets the rest run in parallel (planning.md: shared registry files)"
        )
    return out


def registry_problems(d, ents):
    """-> a problem for each task whose text matches a pipeline.json `registry_files` rule but whose Predicted paths lack the registry file: two such tasks in one wave would both edit it (one lists it, the other does not, so the overlap rule never saw them) and conflict at the merge. Listing it makes the overlap rule give them separate waves."""
    try:
        rules = registry_files_of(project(_workspace(d)))
    except ConfigError:
        return []
    out = []
    for e in ents:
        text = read_text(os.path.join(d, f"{e['id']}.md"))
        listed = task_paths(d, e["id"])
        out += [
            f"{e['id']}: its text matches the `registry_files` rule `{r['when']}` but Predicted paths does not list {r['path']}: list it (tasks that edit it never share a wave)"
            for r in rules
            if re.search(r["when"], text, re.I) and not any(_same(p, r["path"]) for p in listed)
        ]
    return out


def _stage_produced(d, ents):
    """-> every Predicted path of the stage's tasks: the paths a command of the stage may name because the plan makes them."""
    return [p for e in ents for p in task_paths(d, e["id"])]


PUBLISH_PROBLEM_PREFIX = "publish: "


def _publish_scope(entry):
    """-> the paths whose change republishes ENTRY: its cwd dir, the path arguments of its build command (the course or app folder it builds, a build script), its page and its published files. A build argument may sit below the cwd, so the cwd alone misses a nested-repo folder."""
    scope = [entry[PUBLISH_PAGE], *entry[PUBLISH_FILES].values()]
    cwd = entry[PUBLISH_CWD].strip("./")
    if cwd:
        scope = [cwd]
    for word in entry[PUBLISH_BUILD].split():
        path = word.removeprefix("./").rstrip("/")
        if "/" in path and not word.startswith(("-", "/")) and PUBLISH_OUT not in word:  # a bare word (`./tool`) is a script the cwd covers
            scope.append(path)
    return scope


def _feature_paths(d, ents):
    """-> the Predicted paths of this stage, and of every phase of its feature (a PUBLISH line sits in the last phase, the paths of the course may be in an earlier one)."""
    paths = _stage_produced(d, ents)
    root = feature_root(d)
    for t in tasks_files(root)[1:]:
        pd = os.path.dirname(t)
        if os.path.abspath(pd) != os.path.abspath(d):
            paths += [p for e in read_entries(pd) for p in task_paths(pd, e["id"])]
    return paths


def publish_problems(d, ents):
    """-> [`publish:` problem] when the stage (not a mid phase) or its feature changes paths of a `publish` entry of pipeline.json and no TASKS.md of the feature carries a `PUBLISH:` line or the flag `no-publish`: the republish step would not run and the artifact stays stale. A bad publish config yields none."""
    try:
        entries = publish_entries(project(_workspace(d)))
    except ConfigError:
        return []
    if is_mid_phase(tasks_path(d)):
        return []  # the last phase republishes; its check sees every phase's paths
    files = tasks_files(feature_root(d))
    if any(publish_urls(read_text(t)) or not parse_flags(t)["publish"] for t in files):
        return []
    paths = _feature_paths(d, ents)
    hit = sorted(u for u, e in entries.items() if any(_same(p, s) for p in paths for s in _publish_scope(e)))
    if not hit:
        return []
    return [
        f"{PUBLISH_PROBLEM_PREFIX}the plan changes paths of the `publish` entry {', '.join(hit)} but no TASKS.md has a `PUBLISH: <url>` line: add it so the artifact is republished, or set `flags: no-publish` when the artifact must stay as it is"
    ]


UX_FLOWS_WARN_PREFIX = "ux-flows: "  # a warning line: never fails a check (errors_of)


def ux_flows_findings(d, ents, repo):
    """-> problems for the configured `ux_flows` file (missing and not made by the plan, or invalid) and a `ux-flows:` warning naming the UI files of the plan that no flow covers. [] when none is configured."""
    cfg = project(_workspace(d))
    v = variables(read_text(tasks_path(d)))
    path, rel = flows_file(repo, cfg, v), flows_rel(cfg, v)
    if not path or "<" in rel:
        return []  # none configured, or a `<name>` placeholder the stage does not fill (UX-VARS): nothing to check yet
    if path_problem(rel):
        return [f"ux_flows: {path_problem(rel)}"]
    produced = _stage_produced(d, ents)
    if not os.path.exists(path):
        return [] if any(_same(p, rel) for p in produced) else [f"ux_flows: {rel} is missing in the repo and no task creates it"]
    flows, probs = read_flows(path)
    if probs:
        return [f"ux_flows: {p}" for p in probs]
    globs = ui_paths(cfg)
    changed = [p for p in produced if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.basename(p), g) for g in globs) and not _same(p, rel)]
    _, loose = covering(flows, changed)
    if not loose:
        return []
    return [
        f"{UX_FLOWS_WARN_PREFIX}UI files of the plan that no flow of {rel} covers: {', '.join(sorted(set(loose))[:5])}: add a flow (or a `covers` glob) in the same task, or the audit reports them UNVERIFIED"
    ]


def _limit_problems(d, ents):
    """-> [max_tasks problem] when more tasks than the limit have not started (started tasks do not count: a running phase can take a fix task). Counts what the runner counts (`parse_tasks`: also table rows and `ops-N` steps), not only the plan.py bullets ENTS."""
    limit = int(project(_workspace(d)).get("max_tasks") or 0)
    if not limit:
        return []
    began = started_tasks(d)
    waiting = [t for t in parse_tasks(tasks_path(d)) if t["id"] not in began] or [e for e in ents if e["id"] not in began]
    msg = f"{len(waiting)} tasks not yet started in one stage, the limit is {limit} (max_tasks): split the work into phases (Plan.md) or two stages"
    return [msg] if limit and len(waiting) > limit else []


def check_stage(d, repo=None):
    """-> (task count, problems)."""
    if not os.path.exists(tasks_path(d)):
        return 0, [f"{tasks_path(d)} missing"]
    probs, ents = [], read_entries(d)
    probs += _limit_problems(d, ents)
    probs += check_docs_target(tasks_path(d), _workspace(d))
    repo = repo or _repo_root(d)
    parsed = {t["id"]: t for t in parse_tasks(tasks_path(d))}
    ids = {e["id"] for e in ents}
    for e in ents:
        if parsed.get(e["id"], {}).get("wave") != e["wave"]:
            probs.append(f"{e['id']}: Graph wave does not match wave W{e['wave']}")
    for t in parsed:
        if t not in ids:
            probs.append(f"{t}: TASKS.md entry not in plan.py format")
    gates = protected_gates(_workspace(d), project(_workspace(d)))
    git = git_state(repo, [p for e in ents for p in task_paths(d, e["id"])])
    produced = _stage_produced(d, ents)
    for e in ents:
        probs += _task_file_problems(d, e, ents, repo, gates, git, produced)
    return len(ents), probs + _overlap_problems(d, ents) + _carried_problems(d, ents) + publish_problems(d, ents) + shared_warnings(
        d, ents
    ) + parallel_candidates(d, ents) + shared_file_errors(_listings(d, ents)) + ux_vars_errors(d, repo) + ux_flows_findings(d, ents, repo) + registry_problems(
        d, ents
    )


def _carried_problems(d, ents):
    """A phase after the first: TASKS.md needs a `## Carried from earlier phases` section; each bullet in it names a task of this stage or says `not needed: reason`."""
    if not is_later_phase(tasks_path(d)):
        return []
    heading = f"## {CARRIED_SECTION}"
    lines = Path(tasks_path(d)).read_text(encoding="utf-8", errors="replace").splitlines()
    if heading not in lines:
        return [f"{heading} section missing: list each item carried from earlier phases as a task or `{CARRIED_NOT_NEEDED} reason`"]
    body = []
    for x in lines[lines.index(heading) + 1 :]:
        if x.startswith("## "):
            break
        if x.startswith("- "):
            body.append(x)
    if not body:
        return [f"{heading} section has no items: write one `- task-N ...` or `- {CARRIED_NOT_NEEDED} reason` line per carried item"]
    ids = {e["id"] for e in ents}
    probs = []
    for x in body:
        named = {t for t in re.findall(r"task-\d+", x) if t in ids}
        if CARRIED_NOT_NEEDED in x and x.split(CARRIED_NOT_NEEDED, 1)[1].strip():
            continue
        if not named:
            probs.append(f"{heading}: `{x[2:]}` is neither a task of this stage nor `{CARRIED_NOT_NEEDED} reason`")
    return probs


GATE_RE = re.compile(r"^gate:\s*(uncertainty gate|owner step or real-data run|separate repo|30-task cap)\b", re.I)
MIN_PHASE, MAX_COMBINED = 10, 25


def errors_of(probs):
    """PROBS without the `combine:`, `size:`, `shared:`, `parallel:`, `gate:`, `release:`, `done:`, `cmd:`, `open-edit:`, `neg:`, `prereq:`, `webtest:`, `tests-use:`, `repos:` and `publish:` warnings (those never fail a check). `ci-drift:` is an error."""
    return [
        p
        for p in probs
        if not p.startswith(
            (
                "combine:",
                "size:",
                "shared:",
                "gate:",
                "release:",
                "done:",
                "cmd:",
                "open-edit:",
                "repos:",
                UX_FLOWS_WARN_PREFIX,
                PARALLEL_WARN_PREFIX,
                AFFECTED_WARN_PREFIX,
                NEG_WARN_PREFIX,
                PREREQ_WARN_PREFIX,
                WEBTEST_WARN_PREFIX,
                GENERATED_WARN_PREFIX,
                TESTCMD_WARN_PREFIX,
            )
        )
    ]


def ci_drift_warnings(d):
    """-> [`ci-drift:` problem, an error in cs-plan check] for the project that owns .work folder D (see ci_drift.drift; a bad config yields none)."""
    ws = _workspace(d)
    try:
        cfg = project(ws)
        repo = repo_dir(ws, cfg)
        return [f"ci-drift: {w}" for w in drift(repo, cfg)]
    except ValueError:
        return []


def phase_size_problems(phases):
    """Mechanical plan-size rules over Plan.md: one phase = a stage (error); an estimate < 10 needs a valid `gate:` line (error); adjacent ungated phases whose estimates sum <= 25 (`combine:` warning)."""
    probs = ["Plan.md has one phase: this should be a stage, use stage-init"] if len(phases) == 1 else []
    gated = [bool(GATE_RE.match(p["goal"])) for p in phases]
    for p, g in zip(phases, gated, strict=True):
        if 0 < p["tasks"] < MIN_PHASE and not g:
            probs.append(
                f"{p['dir']}: estimate {p['tasks']} tasks (< {MIN_PHASE}) and the goal has no valid `gate: <reason>` line (uncertainty gate | owner step or real-data run | separate repo | 30-task cap)"
            )
    for i in range(1, len(phases)):
        x, y = phases[i - 1], phases[i]
        if x["tasks"] and y["tasks"] and not gated[i - 1] and not gated[i] and x["tasks"] + y["tasks"] <= MAX_COMBINED:
            probs.append(
                f"combine: {x['dir']} and {y['dir']} have no gate and {x['tasks'] + y['tasks']} estimated tasks together (<= {MAX_COMBINED}): make them one phase"
            )
    return probs


def is_shell(cmd):
    """True when CMD parses as a shell command (`sh -n`); a sentence with `;` or `(` does not."""
    return subprocess.run(["sh", "-n", "-c", cmd], capture_output=True, check=False).returncode == 0


def _done_when_files(a, p, d):
    """Problems for the `done_when` files of phase P (folder D): see plan_rules.done_when_problems."""
    if not p["done_when"]:
        return []
    produced, lead = [], ""
    if os.path.exists(tasks_path(d)):
        lead = "\n".join(line for line in read_text(tasks_path(d)).splitlines() if re.search(r"\bops-\d+", line))
        produced = [x for e in read_entries(d) for x in task_paths(d, e["id"])]
    return done_when_problems(p, repo_dir(a.dir, project(a.dir)), produced, lead)


BUMP_PHASE = re.compile(r"(?i)\bbump\b[^|]*(\d+\.\d+\.\d+|version|changelog)|\bversion bump\b|\brelease v?\d+\.\d|\btag v\d")


def _done_when_commands(a, p, d):
    """Problems and `cmd:` warnings for the command of the done_when cell of phase P (a shell command only): its executable and path arguments must exist or be made by the plan."""
    if not p["done_when"] or not is_shell(p["done_when"]):
        return []
    produced = list(p["paths"])
    if os.path.exists(tasks_path(d)):
        produced += [x for e in read_entries(d) for x in task_paths(d, e["id"])]
    return command_findings(f"{p['dir']} done_when", p["done_when"], repo_dir(a.dir, project(a.dir)), produced)


def _gate_command_problems(a):
    """Problems for the pipeline.json gate commands: each `cmd` runs in the repo, so its executable and path arguments must exist."""
    cfg = project(a.dir)
    repo = repo_dir(a.dir, cfg)
    return [
        m
        for key in (GATES_KEY, MERGE_GATES_KEY, FINAL_GATES_KEY)
        for g in cfg.get(key) or []
        for m in command_findings(f"pipeline.json {GATE_LABELS[key]} `{g['name']}`", g["cmd"], repo)
    ]


def _bump_phase_problems(a, phases):
    if not project(a.dir).get("release_script"):
        return []
    return [
        f"{p['dir']}: plans a version bump or release, but pipeline.json has release_script: the finish phase bumps (target = the vX.Y.Z in the Plan.md goal) and tags itself; drop this phase"
        for p in phases
        if BUMP_PHASE.search(p["dir"] + " " + p["goal"])
    ]


def _done_when_shape_problems(a, p):
    """Problems for the done_when cell of phase P: empty, not a shell command, a sentence, or a leading cd into the repo. `MANUAL: <check>` is accepted as a manual check."""
    if not p["done_when"]:
        return [f"{p['dir']}: no done_when"]
    rest = manual_tag(p["done_when"])
    if rest is not None:
        return [] if rest else [f"{p['dir']}: done_when `MANUAL:` needs the check text after the colon"]
    probs = []
    if not is_shell(p["done_when"]):
        probs.append(f"{p['dir']}: done_when is not a shell command (`sh -n` fails): write one command, not a sentence")
    elif reads_as_prose(p["done_when"]):
        probs.append(f"{p['dir']}: done_when reads as a sentence, not a command: write the command that proves it, or `MANUAL: <what to check>`")
    repo = project(a.dir).get("repo") or ""
    if repo and re.match(rf"\s*cd\s+{re.escape(repo)}\b", p["done_when"]):
        probs.append(f"{p['dir']}: done_when starts with `cd {repo}` but the driver already runs it in {repo}: drop the cd")
    return probs


def _phase_link_problems(p, earlier):
    """Problems for phase P against the phases listed before it: shared paths, unknown or later depends."""
    probs = []
    for q in earlier:
        ov = overlap(p["paths"], q["paths"])
        if ov and not phases_ordered([*earlier, p], p["dir"], q["dir"]):
            probs.append(f"{p['dir']} and {q['dir']} share paths: {ov[0]} / {ov[1]}")
    for dep in p["depends"]:
        if not any(dep in (q["dir"], q["dir"][3:]) for q in earlier):
            probs.append(f"{p['dir']}: depends on unknown or later phase {dep}")
    return probs


def _phase_problems(a, p, d, earlier):
    """-> (task count, problems) for phase P in folder D."""
    probs = _done_when_shape_problems(a, p)
    if manual_tag(p["done_when"]) is None:
        probs += _done_when_files(a, p, d) + _done_when_commands(a, p, d)
    if not os.path.isdir(d):
        probs.append(f"{p['dir']}: folder {d} missing")
    probs += _phase_link_problems(p, earlier)
    if not os.path.exists(tasks_path(d)):
        return 0, probs
    k, pr = check_stage(d)
    return k, probs + [f"{p['dir']}/{m}" for m in pr]


def _plan_head_problems(a, phases):
    """Problems for Plan.md itself: phase sizes, the acceptance checklist."""
    probs = phase_size_problems(phases)
    if not checklist_items(read_text(plan_path(a))):
        probs.append("Plan.md: `## Acceptance checklist` is missing or empty: one `- cmd:` or `- eyes:` line per checkable end-state item")
    return probs + acceptance_problems(read_text(plan_path(a)), repo_dir(a.dir, project(a.dir))) + _gate_command_problems(a)


def check_feature(a, fdir):
    """-> (task count, problems) for Plan.md and every phase folder with a TASKS.md."""
    phases = read_phases(a)
    if not phases and not os.path.exists(os.path.join(fdir, PLAN_FILE)):
        return 0, [f"{fdir}/Plan.md missing"]
    probs, n = _plan_head_problems(a, phases), 0
    for i, p in enumerate(phases):
        k, pr = _phase_problems(a, p, os.path.join(fdir, "phases", p["dir"]), phases[:i])
        n += k
        probs += pr
    probs += _bump_phase_problems(a, phases)
    mds = [t for t in (tasks_path(os.path.join(fdir, "phases", p["dir"])) for p in phases) if os.path.exists(t)]
    return n, probs + release_flag_problems(mds)


def publish_urls(text):
    """-> the set of whole URLs on every `PUBLISH:` line of text."""
    return {u for line in re.findall(PUBLISH_LINE_RE, text, re.I | re.M) for u in re.split(r"[\s,;]+", line) if u}


def feature_root(d):
    """-> the feature folder of D: D itself, or the folder above `phases/` for a phase stage."""
    parent = os.path.dirname(d)
    return os.path.dirname(parent) if os.path.basename(parent) == "phases" else d


def tasks_files(root):
    """-> the TASKS.md paths of the feature folder ROOT: its own, then one per phase."""
    out = [os.path.join(root, TASKS_FILE)]
    phases = os.path.join(root, "phases")
    if os.path.isdir(phases):
        out += [os.path.join(phases, p, TASKS_FILE) for p in sorted(os.listdir(phases))]
    return out


def publish_problems_in(root, goal):
    """-> problem when the request GOAL has a `PUBLISH:` line that no TASKS.md under ROOT carries (the driver's republish step reads only TASKS.md)."""
    if not re.search(PUBLISH_RE, goal, re.I | re.M):
        return []
    have = set().union(*(publish_urls(read_text(t)) for t in tasks_files(root)))
    missing = sorted(publish_urls(goal) - have)
    if not missing:
        return []
    return [
        f"{TASKS_FILE}: the request's `PUBLISH:` line names {', '.join(missing)}; copy it verbatim into {TASKS_FILE} (the republish step reads only that file)"
    ]


def request_problems(d):
    """-> the PUBLISH problems of folder D against the request copy (`request.md`) in its feature folder; [] when there is none."""
    root = feature_root(d)
    return publish_problems_in(root, read_text(os.path.join(root, REQUEST_FILE)))
