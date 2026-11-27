"""Plan-size and gate rules for plan_check: task size cap, protected gates a task touches, untracked Predicted files, `done_when` files, release flag."""

import fnmatch
import glob
import os
import re
import shlex
import subprocess

from . import gitx
from .configuration import (
    ACCEPTANCE_CMD_TAG,
    ACCEPTANCE_MANUAL,
    ACCEPTANCE_PROSE_OPENERS,
    ACCEPTANCE_PROSE_WORDS,
    AFFECTED_CODE_EXTS,
    AFFECTED_MAX_TESTS,
    AFFECTED_MIN_STEM,
    AFFECTED_SHOWN,
    AFFECTED_WARN_PREFIX,
    BARE_TAG_RE,
    CITE_SKIP_DIRS,
    CMD_REF_PREFIX,
    DEFAULT_WORKSPACE,
    DOC_PATHS,
    DOCS_WAVE_DIR,
    DOCS_WAVE_PREFIX,
    DONE_WHEN_EXTS,
    DRYRUN_FIELD,
    DRYRUN_FLAGS,
    GENERATED_WARN_PREFIX,
    NEG_GREP_RE,
    NEG_WARN_PREFIX,
    OUTPUTS_FIELD,
    PER_FILE_CHECK_PHRASES,
    PER_FILE_PHRASES,
    PREREQ_WARN_PREFIX,
    SCRIPT_EXTS,
    SKIP_GATE_MSG,
    SKIP_GATE_RE,
    SKIP_GATE_WARN_PREFIX,
    TASK_DIR_WEIGHT,
    TASK_FILES_FAIL,
    TOOL_FIELD,
    TOOLS_DIR,
    WEBTEST_WARN_PREFIX,
    WORK_DIR,
)
from .generated import generated_among, marker
from .plan_files import PATH_FILE, acceptance_items, paths_in, task_type
from .plan_pins import _is_test_path, _referencing_tests, check_pinned_tests
from .shellwords import split_commands
from .stagedir import workspace_of
from .util import read_text
from .worktree import nested_repo_of, nested_repos

PROTECTED_AUDIT_RE = re.compile(r"(?im)^\W*protected gate\s+([\w .-]+?)[\s*]*:[\s*]*(.+)$")  # also `- **Protected gate x**: a, b` and `| protected gate z: q |`
ABS_PATH_RE = re.compile(r"(?<![\w.])/[\w@%+=:,.~/-]+")
LEAD_OWNER_RE = re.compile(r"(?im)^owner:\s*ops\b")  # a lead step: the lead runs it, a job worktree never does


def _under(path, root):
    return path == root or path.startswith(os.path.join(root, ""))


MAIN_CHECKOUT_MSG = "Test line names an absolute workspace path (it runs in the main checkout, not the job worktree, so a new test file is 'not found'): use a path relative to the repo root"


def main_checkout_problems(test_text, repo=None) -> list[str]:
    """Problems for the text of one `Test:` line: an absolute path inside the main checkout, i.e. under DEFAULT_WORKSPACE or REPO. The dev runs in a job worktree, so such a path runs the main tree or nothing. Exempt: paths under TOOLS_DIR (qrun, ptest) and under a WORK_DIR folder of a root (scratch output). Shared by brieflint and plan.py check."""
    roots = {os.path.normpath(r) for r in (DEFAULT_WORKSPACE, repo) if r}
    for tok in ABS_PATH_RE.findall(test_text):
        full = os.path.normpath(tok)
        if _under(full, os.path.normpath(TOOLS_DIR)):
            continue
        if any(_under(full, root) and not _under(full, os.path.join(root, WORK_DIR)) for root in roots):
            return [MAIN_CHECKOUT_MSG]
    return []


def nested_repo(repo, path, repos=None) -> str:
    """-> the relative dir of the longest ancestor of PATH (below REPO, REPO itself excluded) that is a nested git repo, else "". One definition of a nested repo: worktree.nested_repos (REPOS = its result when the caller has it, so a loop over paths walks the tree once)."""
    return nested_repo_of(path, nested_repos(repo) if repos is None else repos)


def check_nested_repo_paths(d, tid, repo=None, prefix="") -> list[str]:
    """Problems for one task file: a Predicted path under a nested-repo dir (PREFIX, pipeline.json `nested_repo_dir_prefix`) that is in no nested git repo of REPO. Job worktrees hold no nested repos unless wt.py gives one a worktree, so otherwise only a lead step (`owner: ops`) may name one. "" = rule off."""
    if not prefix:
        return []
    text = read_text(os.path.join(d, tid + ".md"))
    if LEAD_OWNER_RE.search(text):
        return []
    repos = nested_repos(repo) if repo else []
    hit = next((p for p in paths_in(text) if p.startswith(prefix) and not nested_repo_of(p, repos)), None)
    return (
        [f"{tid}: Predicted path {hit} is in a nested repo with no nested git repo (job worktrees hold none): make it a lead step (`owner: ops`) or drop it"]
        if hit
        else []
    )


OPEN_EDIT_RE = re.compile(r"(?:outside|beyond|other than|not in|not among)\s+(?:the\s+)?(?:predicted|listed|named)\s+(?:paths?|files?)\b", re.I)


def check_open_edit(d, tid, prefix="") -> list[str]:
    """`open-edit:` warning for one task file: the text lets the dev edit files outside Predicted paths ("if it lives outside Predicted paths, edit it"). The dev cannot reach a file in a nested repo (PREFIX, `nested_repo_dir_prefix`) from the job worktree, so the architect names every file; `repos:` warning when Predicted paths are in both the project repo and a nested repo."""
    text = read_text(os.path.join(d, tid + ".md"))
    out = []
    if m := OPEN_EDIT_RE.search(text):
        out.append(
            f"open-edit: {tid}: {m[0]!r} lets the dev edit files nobody listed; a file in a nested repo cannot be edited from the job worktree: name every file under Predicted paths (find it in an earlier task, or give the nested-repo file its own task)"
        )
    paths = paths_in(text)
    if prefix and any(p.startswith(prefix) for p in paths) and any(not p.startswith(prefix) for p in paths):
        out.append(f"repos: {tid}: Predicted paths are in the project repo and in a nested repo ({prefix}...): one task per repo")
    return out


WEBTEST_BY_RE = re.compile(r"(?i)\b(?:run|driven|done|checked|executed)\s+by\s+the\s+web-?tester\b|\bweb-?tester\s+(?:runs|must run|drives)\b")


def check_webtest_type(d, tid) -> list[str]:
    """`webtest:` warning (never fails a check): the brief says the web-tester runs the task but its `Type:` is not `webtest`. A dev cannot drive a browser; the driver would give the task to a dev, who can only reply BLOCKED."""
    text = read_text(os.path.join(d, tid + ".md"))
    if task_type(text) != "webtest" and (m := WEBTEST_BY_RE.search(text)):
        return [
            f"{WEBTEST_WARN_PREFIX} {tid}: says {m[0]!r} but is not `Type: webtest`: the driver would give it to a dev, who cannot drive a browser; add the header line `Type: webtest`"
        ]
    return []


def _tracked(repo, rels):
    """-> the paths of RELS that git tracks in REPO; [] when REPO is no git work tree."""
    try:
        r = gitx.run(repo, "ls-files", "-z", "--", *rels)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [f for f in r.stdout.split("\0") if f] if not r.returncode else []


def check_generated_paths(d, tid, repo) -> list[str]:
    """`generated:` warning (never fails a check): Predicted paths names a tracked file whose header says it is generated (setting `generated_marker`). The dev must regenerate it with its generator, not edit it by hand; list the source file instead."""
    rels = [p for p in paths_in(read_text(os.path.join(d, tid + ".md"))) if not p.endswith("/")]
    hits = generated_among(repo, _tracked(repo, rels), marker(workspace_of(d)))
    return [
        f"{GENERATED_WARN_PREFIX} {tid}: Predicted paths lists generated file {h}: list its source instead; the dev regenerates it with the generator named in its header"
        for h in hits
    ]


def check_affected_tests(d, tid, repo=None) -> list[str]:
    """`tests-use:` warning (never fails a check; the `pins:` errors of plan_pins come with it): a non-test Predicted path whose file name is used by test files that neither the task's `Affected tests:` line, its Predicted paths nor its Test lines name; the dev would find them only when the gate goes red. Skipped for `Type: report` and for a task whose Predicted paths are all test files. Code files only; a name more than AFFECTED_MAX_TESTS tests mention is too common and gives nothing. A name matches as a whole word (a string search, not a call graph): a warning, not proof."""
    text = read_text(os.path.join(d, tid + ".md"))
    if not repo or task_type(text) == "report":
        return []
    out = check_pinned_tests(d, tid, repo)  # `pins:` errors ride with this check
    paths = [p for p in paths_in(text) if not p.endswith("/") and not glob.has_magic(p)]
    src = [p for p in paths if p.endswith(AFFECTED_CODE_EXTS) and not _is_test_path(p)]
    if not src:
        return out
    rels = ["", *nested_repos(repo)]
    for p in src:
        stem = os.path.splitext(os.path.basename(p))[0]
        users = _unlisted_users(repo, rels, p, stem, text)
        if users:
            out.append(_affected_line(tid, p, stem, users))
    return out  # the `tests-use:` warnings, plus the `pins:` errors from plan_pins


def _unlisted_users(repo, rels, p, stem, text):
    """The test files that mention STEM (the name of predicted file P) and are not named anywhere in the task TEXT; none for a too short, too common name."""
    if len(stem) < AFFECTED_MIN_STEM:
        return []
    users = _referencing_tests(repo, rels, stem)
    if len(users) > AFFECTED_MAX_TESTS:
        return []
    return [t for t in users if t != p and t not in text and os.path.basename(t) not in text]


def _affected_line(tid, p, stem, users):
    """The `tests-use:` warning for one predicted file P and the test files USERS that mention its name STEM."""
    return f"{AFFECTED_WARN_PREFIX} {tid}: {', '.join(users[:AFFECTED_SHOWN])} mention {stem!r} ({p}) but the task does not name them: list them under `Affected tests:` (or `none` if they do not assert the old behaviour)"


def check_negative_tag(d, tid) -> list[str]:
    """`neg:` warning (never fails a check): a Done/Test line bans a bare markup tag fragment with a negative grep (`! grep -q "<script"`). Such a criterion also blocks a later fix that legitimately needs the tag; the architect narrows it with an attribute or a path."""
    out = []
    for ln in read_text(os.path.join(d, tid + ".md")).splitlines():
        if re.search(NEG_GREP_RE, ln) and (m := re.search(BARE_TAG_RE, ln)):
            tag = re.sub(r"[\\\"']+$", "", m[0])
            out.append(
                f"{NEG_WARN_PREFIX} {tid}: {ln.strip()[:80]!r} bans the bare tag {tag!r}: narrow it (an attribute such as `<script src=` or a path), a later fix may need the tag"
            )
    return out


def check_skip_gate(d, tid) -> list[str]:
    """`skip-gate:` warning (never fails a check): the task file says a test is skipped (`pytest.mark.skipif`, `pytest.skip(`). cs-qrun exits 3 on a skipped test, so a skip gate reads as not green; a registered marker the conftest deselects is the fix."""
    if re.search(SKIP_GATE_RE, read_text(os.path.join(d, tid + ".md"))):
        return [f"{SKIP_GATE_WARN_PREFIX} {tid} says skipif/pytest.skip: {SKIP_GATE_MSG}"]
    return []


TASK_MENTION_RE = re.compile(r"(?<![\w./-])task-(\d+)(?![\w/-])")  # a path part such as /x/task-1/repo (job worktree folder) is not a mention
OTHER_SCOPE_RE = re.compile(r"\btask-\d+\s+(?:of|in)\s+(?:phase|stage)\b", re.I)  # `task-3 of phase 01`: a task of another phase, not of this stage


def _transitive_deps(ents, tid):
    """-> the ids TID depends on, directly or through other tasks."""
    by = {e["id"]: e for e in ents}
    seen: set = set()
    todo = list(by[tid]["depends"]) if tid in by else []
    while todo:
        t = todo.pop()
        if t not in seen:
            seen.add(t)
            todo += by[t]["depends"] if t in by else []
    return seen


def check_unlisted_prereq(d, e, ents) -> list[str]:
    """`prereq:` warning (never fails a check): the task file names another task of the stage in the same wave or a later one that the task does not depend on, directly or through others. Tasks of one wave start together from the same base, so work the brief needs from such a task is not there; add it with `cs-plan edit-task --id N --depends M`, which moves the task to the wave after M (an earlier wave is merged before this task starts, so it needs no line)."""
    tid, wave = e["id"], e["wave"]
    by = {x["id"]: x for x in ents}
    body = re.split(r"(?m)^## ADDENDUM", read_text(os.path.join(d, tid + ".md")), maxsplit=1)[
        0
    ]  # a retry addendum is the lead's note of the moment, not the plan
    text = "\n".join(ln for ln in body.splitlines() if not ln.startswith(("#", "Wave:", "Pattern:", "Depends:")))
    named = {f"task-{n}" for n in TASK_MENTION_RE.findall(OTHER_SCOPE_RE.sub("", text))} - {tid}
    held = _transitive_deps(ents, tid)
    late = sorted((t for t in named if t in by and by[t]["wave"] >= wave and t not in held), key=lambda t: int(t[5:]))
    return (
        [
            f"{PREREQ_WARN_PREFIX} {tid}: names {', '.join(late)} (same or later wave) but does not depend on it: if it needs that work, `cs-plan edit-task --id {tid[5:]} --depends {late[0][5:]}` (moves {tid} to the wave after it)"
        ]
        if late
        else []
    )


def _is_docs_path(path):
    return any(fnmatch.fnmatch(path, g) for g in (*DOC_PATHS, DOCS_WAVE_DIR + "*"))


def check_docs_wave(d, e, ents) -> list[str]:
    """`docs-wave:` error: every Predicted path of the task is a docs path (`*.md` or under `docs/`) and a code task of the same wave is not among its dependencies, direct or through others. The docs then run beside the code they describe and may read it before it lands; add the dependency with `cs-plan edit-task --id N --depends M`, which moves the docs task to the wave after M."""
    tid, wave = e["id"], e["wave"]
    own = paths_in(read_text(os.path.join(d, tid + ".md")))
    if not own or not all(_is_docs_path(p) for p in own):
        return []
    held = _transitive_deps(ents, tid)
    code = sorted(
        (
            x["id"]
            for x in ents
            if x["wave"] == wave
            and x["id"] != tid
            and x["id"] not in held
            and any(not _is_docs_path(p) for p in paths_in(read_text(os.path.join(d, x["id"] + ".md"))))
        ),
        key=lambda t: int(t[5:]),
    )
    return (
        [
            f"{DOCS_WAVE_PREFIX} {tid}: all Predicted paths are docs but {', '.join(code)} (same wave, code) is not a dependency: if the docs need that work, `cs-plan edit-task --id {tid[5:]} --depends {code[0][5:]}` (moves {tid} to the wave after it)"
        ]
        if code
        else []
    )


DOCS_LINE_RE = re.compile(r"^\s*docs\s*:\s*\S", re.I | re.M)


def check_docs_target(tasks_md, workspace) -> list[str]:
    """Problem when TASKS.md has the `docs` flag but the final-docs step would find no target: a `docs:` line in TASKS.md or PROGRESS.md, or an existing `docs/` folder in the workspace. A `## Docs` section in CLAUDE.md alone does not count (it may say "No docs folder yet"). Without one the step ends BLOCKED, so the plan check stops it earlier."""
    from .tasks import parse_flags

    if not parse_flags(tasks_md)["docs"]:
        return []
    if DOCS_LINE_RE.search(read_text(tasks_md)) or DOCS_LINE_RE.search(read_text(os.path.join(workspace, "PROGRESS.md"))):
        return []
    if os.path.isdir(os.path.join(workspace, "docs")):
        return []
    return [
        "flags: docs is set but there is no docs target: no `docs/` folder in the workspace and no `docs: <folder>` (or `docs: skip`) line in TASKS.md or PROGRESS.md"
    ]


BULK_RE = re.compile(r"\b(\d[\d,]*)\s+(?:[\w-]+\s+){0,2}?(?:rows|items|cards|prompts|answers|findings|sentences|ids|entries|questions)\b", re.I)
BULK_MIN = 100


def _bulk_warning(tid, text):
    """`size:` warning when a hand-edit task names BULK_MIN or more rows/items (not a script or report task)."""
    if task_type(text) == "report" or "script" in text.split("\n", 1)[0].lower():
        return []
    for m in BULK_RE.finditer(text):
        if int(m[1].replace(",", "") or 0) >= BULK_MIN:
            return [f"size: {tid}: names {m[0]!r}: split by id range (disjoint rows share a wave with (rows)), at most ~60 rows per task"]
    return []


def check_task_size(d, tid) -> list[str]:
    """Problems for one task file: more than TASK_FILES_FAIL predicted files (error); a `size:` warning for a hand edit over BULK_MIN rows."""
    text = read_text(os.path.join(d, tid + ".md"))
    n = sum(TASK_DIR_WEIGHT if p.endswith("/") or glob.has_magic(p) else 1 for p in set(paths_in(text)))
    if n > TASK_FILES_FAIL:
        return [f"{tid}: {n} predicted files (> {TASK_FILES_FAIL}): too big for one dev, architect: split by seam (one concern or module per task)"]
    return _bulk_warning(tid, text)


def protected_gates(workspace, cfg) -> list[tuple[str, list[str]]]:
    """-> [(name, [glob])] from pipeline.json `protected_gates` ([{name, paths}]) and `protected gate <name>: <globs>` lines of the audit file."""
    out = []
    for g in cfg.get("protected_gates") or []:
        if isinstance(g, dict) and g.get("name") and g.get("paths"):
            out.append((str(g["name"]), [str(x) for x in g["paths"]]))
    audit = os.path.join(workspace, str(cfg.get("audit_file") or "AUDIT.md"))
    for m in PROTECTED_AUDIT_RE.finditer(read_text(audit) if os.path.exists(audit) else ""):
        out.append((m[1].strip(), [x.strip("`>| ") for x in m[2].split("|")[0].replace("**", "").split(",") if x.strip("`>| ")]))
    return out


def _covers(glob_, path):
    g = glob_.rstrip("/")
    return fnmatch.fnmatch(path, g) or path.startswith(g + "/") or fnmatch.fnmatch(os.path.basename(path), g)


def check_gate_mentions(d, tid, gates) -> list[str]:
    """`gate:` warnings (never fail a check) for one task: a Predicted path covered by a protected gate whose name the task text does not mention (the dev finds the insert-only gate only when the suite goes red)."""
    text = read_text(os.path.join(d, tid + ".md"))
    paths, probs = paths_in(text), []
    for name, globs in gates:
        hit = next((p for p in paths for g in globs if _covers(g, p)), None)
        if hit and name.lower() not in text.lower():
            probs.append(
                f"gate: {tid}: edits {hit}, covered by protected gate `{name}`, but the task does not name it: state the gate and its expected new behaviour"
            )
    return probs


def _repo_paths(paths):
    return [p for p in dict.fromkeys(paths) if not os.path.isabs(p) and ".." not in p.split("/")]


def _repo_state(repo, paths):
    """-> (tracked files set, ignored PATHS set) for one repo, or None when REPO is no git work tree or git fails."""
    try:
        if gitx.run(repo, "rev-parse", "--is-inside-work-tree").returncode:
            return None  # no repo: check_git_paths reports it
        tracked = set(gitx.run(repo, "ls-files", "-z").stdout.split("\0"))
        ignored = set()
        if paths:
            r = gitx.run(repo, "check-ignore", "--stdin", "-z", input="\0".join(paths) + "\0")
            ignored = set(r.stdout.split("\0"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return tracked, ignored


def _by_repo(repo, paths):
    """-> {nested repo dir ("" = REPO): [path relative to that repo]} for the repo-relative PATHS."""
    groups: dict[str, list[str]] = {"": []}
    repos = nested_repos(repo)
    for p in _repo_paths(paths):
        sub = nested_repo_of(p, repos)
        groups.setdefault(sub, []).append(p[len(sub) + 1 :] if sub else p)
    return groups


def git_state(repo, paths) -> dict[str, tuple[set[str], set[str]] | None]:
    """-> {nested repo dir ("" = REPO): (tracked set, ignored set) or None}: `ls-files -z` and `check-ignore --stdin -z` once per repo, run inside the nested repo for a path under one; None for a repo that is no git work tree or whose git fails."""
    return {sub: _repo_state(os.path.join(repo, sub), ps) for sub, ps in _by_repo(repo, paths).items()}


def untracked_problems(paths, repo, state=None) -> list[str]:
    """Problems (no task id prefix) for Predicted PATHS: one that exists but is untracked, or that git ignores (the dev's commit would not hold it), judged in the nested repo that holds it. A path a task has yet to create does not exist, so it is not flagged. STATE = git_state() over the whole stage (else computed here)."""
    probs = []
    state = git_state(repo, paths) if state is None else state
    for sub, ps in _by_repo(repo, paths).items():
        if state.get(sub) is None:
            continue
        tracked, ignored_set = state[sub]
        for p in ps:
            shown = f"{sub}/{p}" if sub else p
            ignored = p in ignored_set
            untracked = os.path.exists(os.path.join(repo, shown)) and not any(t == p or t.startswith(p.rstrip("/") + "/") for t in tracked)
            if ignored:
                probs.append(f"Predicted path {shown} is ignored by git (the dev cannot commit it): drop it or plan it by absolute path outside the repo")
            elif untracked:
                probs.append(f"Predicted path {shown} exists but is untracked in git (a job worktree will not hold it): commit it first or drop it")
    return probs


def check_untracked(d, tid, repo, state=None) -> list[str]:
    """Problems for one task file: see untracked_problems(). STATE = git_state() over the whole stage (else computed here)."""
    return [f"{tid}: {m}" for m in untracked_problems(paths_in(read_text(os.path.join(d, tid + ".md"))), repo, state)]


FILE_TOKEN_RE = re.compile(rf"(?<![\w./$~-])({PATH_FILE})(?![\w/*?])")
QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
MODULE_ARG_RE = re.compile(r"(?<!\S)-m\s+\S+")
TEE_APPEND = "-a"  # `tee -a FILE`: the flag is not the file


def _split_words(cmd):
    """Shell words of `cmd`; an unbalanced quote falls back to whitespace split."""
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _target(words, i, attached):
    """The file a write marker names: the text glued to the marker, else the next word (`tee -a` skips its flag)."""
    if attached:
        return attached
    if i + 1 < len(words) and words[i + 1] == TEE_APPEND:
        i += 1
    return words[i + 1] if i + 1 < len(words) else ""


def written_files(cmd) -> set[str]:
    """Files the command itself writes (`>`, `>>`, `tee [-a]`, `-o`, `--output`), with trailing `;&|)` removed."""
    out = set()
    words = _split_words(cmd)
    for i, w in enumerate(words):
        if ">" in w:
            hit = _target(words, i, w[w.index(">") :].lstrip(">"))
        elif w == "tee" or w.endswith(("|tee", "(tee")):
            hit = _target(words, i, "")
        elif w.startswith("--output"):
            rest = w[len("--output") :]
            hit = rest[1:] if rest.startswith("=") else (_target(words, i, "") if not rest else "")
        elif w.startswith("-o"):
            hit = _target(words, i, w[2:])
        else:
            hit = ""
        if hit:
            out.add(hit.strip(";&|)"))
    return out


def _same(a, b):
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a) or a.startswith(b + "/") or fnmatch.fnmatch(a, b)


def _is_file_token(tok):
    return "/" in tok or tok.rsplit(".", 1)[-1].lower() in DONE_WHEN_EXTS


def done_when_problems(phase, repo, produced, lead_text) -> list[str]:
    """Problems for one Plan.md phase: a file named in `done_when` that does not exist and that no Predicted/owned path, no lead step (ops-N line, LEAD_TEXT) and no `>`/`-o`/`tee` of the command produces. A path with `/` is an error; a bare name is a `done:` warning (it may be a tool argument). Quoted strings, `-m` arguments, absolute paths and names without `/` or a known extension are skipped."""
    cmd = MODULE_ARG_RE.sub(" ", QUOTED_RE.sub(" ", phase["done_when"]))
    written = written_files(cmd)
    probs = []
    for tok in sorted(set(FILE_TOKEN_RE.findall(cmd))):
        if (
            tok.startswith(("-", "/", CMD_REF_PREFIX))
            or not _is_file_token(tok)
            or any(_same(tok, w) for w in written)
            or os.path.exists(os.path.join(repo, tok))
        ):
            continue
        if any(_same(tok, q) for q in [*produced, *phase["paths"]]) or tok in lead_text:
            continue
        msg = f"{phase['dir']}: done_when needs {tok}, but no Predicted path produces it and no lead step names it: add the path to a task or an ops step"
        probs.append(msg if "/" in tok else "done: " + msg)
    for w in glob_words(cmd):
        if not glob_hits(repo, w, [*produced, *phase["paths"], *written]):
            probs.append(f"{phase['dir']}: done_when glob `{w}` matches no file and no Predicted path or lead step produces one: check the name")
    return probs


ACCEPT_ENV_RE = re.compile(r"^[A-Za-z_]\w*=")
ACCEPT_CD_RE = re.compile(r"(?:^|[;&|]\s*)cd(?:\s|$)")


def reads_as_prose(text) -> bool:
    """True when TEXT is a sentence, not a command, judged from the words alone (never from this host's PATH, so a plan checks the same everywhere): two or more sentence words, a sentence word or opener as the first word, or a full stop at the end. A word that is an option, a path, an assignment or holds a quote or shell character means a command."""
    words = text.split()
    if len(words) < 2 or any(w.startswith("-") or any(c in w for c in "/=$`|<>;&('\"") for w in words):
        return False
    low = [w.lower() for w in words]
    return text.endswith(".") or low[0] in ACCEPTANCE_PROSE_OPENERS or sum(w in ACCEPTANCE_PROSE_WORDS for w in low) >= 2


def manual_tag(text) -> str | None:
    """-> the text after `MANUAL:` when `done_when` TEXT starts with that tag (a check a person runs, not a shell command), else None. An empty rest is a tag with no check."""
    head, sep, rest = text.partition(":")
    return rest.strip() if sep and head.strip() == ACCEPTANCE_MANUAL else None


PATH_TEST_FLAGS = ("-f", "-e", "-s", "-d")
PATH_GREP_FLAGS = ("-q", "-c")


def _is_path_test(args):
    """`test -f|-e|-s|-d X` (args after `test`, or after `[` with the closing `]` removed)."""
    return len(args) == 2 and args[0] in PATH_TEST_FLAGS


def _is_path_ls(args):
    """`ls X` or `stat X`: one operand, no option."""
    return len(args) == 1 and not args[0].startswith("-")


def _is_path_grep(args):
    """`grep -q|-c NAME FILE`: only the quiet or count option, two operands."""
    opts = [a for a in args if a.startswith("-")]
    operands = [a for a in args if not a.startswith("-")]
    return bool(opts) and all(o in PATH_GREP_FLAGS for o in opts) and len(operands) == 2


# command name -> predicate over its arguments; a command not listed here never only tests a path
PATH_ONLY_PREDICATES = {"test": _is_path_test, "ls": _is_path_ls, "stat": _is_path_ls, "grep": _is_path_grep}


def _path_only_segment(words):
    """True when WORDS (one command segment, split on blanks) only tests that a path exists: `test -f|-e|-s|-d X`, `[ -f X ]`, `ls X`, `stat X`, `grep -q|-c NAME FILE`."""
    name, args = words[0], words[1:]
    if name == "[":
        if not args or args[-1] != "]":
            return False
        name, args = "test", args[:-1]
    predicate = PATH_ONLY_PREDICATES.get(name)
    return predicate is not None and predicate(args)


def _only_tests_path(text):
    """True when every segment of TEXT (split at `&&`, `||`, `;`, `|`, `&`, newline) only tests that a path exists: a check that runs no behaviour."""
    segs = [s.split() for s in split_commands(text)]
    segs = [w for w in segs if w]
    return bool(segs) and all(_path_only_segment(w) for w in segs)


GLOB_WORD_RE = re.compile(r"[\w.*?][\w./*?-]*")  # a path-like shell word: no leading `/` or `-`, no quotes, no `(`


def glob_words(text) -> list[str]:
    """Shell words of TEXT that are file globs (a `*` or `?` in a path-like word); `print(1*2)`, options and full ref names (`refs/tags/v*`) are not."""
    return [w for w in _split_words(text) if GLOB_WORD_RE.fullmatch(w) and re.search(r"[*?]", w) and not w.startswith(CMD_REF_PREFIX)]


def glob_matches(repo, pattern) -> list[str]:
    """Repo-relative files that PATTERN matches at any depth under REPO (`test_x*` finds `a/tests/test_x1.py`)."""
    pat = pattern.removeprefix("./")
    out = []
    for root, dirs, files in os.walk(repo):  # glob.glob skips dot folders such as .claude
        dirs[:] = [d for d in dirs if d not in CITE_SKIP_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo).replace(os.sep, "/")
            if fnmatch.fnmatch(rel if "/" in pat else f, pat):
                out.append(rel)
    return out


def glob_hits(repo, pattern, names=()) -> list[str]:
    """What PATTERN matches: the repo files (`glob_matches`) plus the NAMES (Predicted paths, written files) it matches. An empty list means the glob names no file."""
    return glob_matches(repo, pattern) + [n for n in names if fnmatch.fnmatch(n, pattern)]


def glob_problems(text, repo, test_only) -> list[str]:
    """Problems for the glob words of one command TEXT, judged over REPO: a glob that matches no file; with TEST_ONLY also a glob that matches only non-test files."""
    probs = []
    for w in glob_words(text):
        hits = glob_hits(repo, w)
        if not hits:
            probs.append(f"glob `{w}` matches no file (check the name and directory)")
        elif test_only and not any(_is_test_path(h) for h in hits):
            probs.append(f"glob `{w}` matches only non-test files (name a test file)")
    return probs


def _accept_cmd_problems(text, repo):
    """-> problem words for one `cmd:` text: the agents' sandbox denies `cd`, `&&` and an env prefix, and a job worktree is not the main checkout."""
    if not text:
        return ["has no command"]
    probs = []
    if ACCEPT_ENV_RE.match(text):
        probs.append("starts with an env assignment")
    if ACCEPT_CD_RE.search(text):
        probs.append("uses `cd`")
    if "&&" in text:
        probs.append("chains with `&&`")
    if main_checkout_problems(text, repo):
        probs.append("names an absolute main-checkout path")
    if reads_as_prose(text):
        probs.append("reads as a sentence, not a command (write the command that proves it, or use `eyes:`)")
    if _only_tests_path(text):
        probs.append("only tests that a path exists (write the command that runs the behaviour, or use `eyes:`)")
    if repo:
        probs += glob_problems(text, repo, test_only=True)
    return probs


def acceptance_problems(plan_md_text, repo=None) -> list[str]:
    """Problems for the `## Acceptance checklist` lines: each starts with `cmd:` or `eyes:`; a cmd is one plain shell command. A missing section is reported elsewhere."""
    probs = []
    for tag, text in acceptance_items(plan_md_text):
        if not tag:
            probs.append(f"Plan.md checklist: `{text[:60]}` starts with neither `cmd:` nor `eyes:`")
        elif tag == ACCEPTANCE_CMD_TAG:
            probs += [f"Plan.md checklist: cmd `{text[:60]}` {w}" for w in _accept_cmd_problems(text, repo)]
    return probs


def release_flag_problems(tasks_mds) -> list[str]:
    """`release:` warnings (never fail) over the TASKS.md of a feature's phases (in order): `no-release` set in a non-final phase but not in the final one. The driver reads the release flag from the final phase alone, so the flag would be dropped."""
    seen = [(m, bool(re.search(r"(?im)^\s*flags\s*:.*\bno-release\b", read_text(m)))) for m in tasks_mds]
    if len(seen) > 1 and not seen[-1][1] and any(v for _, v in seen[:-1]):
        on = [os.path.basename(os.path.dirname(m)) for m, v in seen[:-1] if v]
        return [
            f"release: `no-release` is set only in {', '.join(on)}, not in the final phase; the release flag is read from the final phase alone: set it there too, or drop it"
        ]
    return []


def _field(text, name):
    """Value of the first `<name>: ...` line of a task file's text, '' when absent."""
    m = re.search(rf"(?im)^{re.escape(name)}:[ \t]*(.*)$", text)
    return m[1].strip() if m else ""


def is_per_file_task(text) -> bool:
    """Heuristic (phrases in configuration PER_FILE_PHRASES): the Goal/Done text says to decide or process each file. The Test, Outputs, Tool and Dry run lines are not searched."""
    body = "\n".join(
        ln for ln in text.splitlines() if not re.match(rf"(?i)({'|'.join(map(re.escape, (OUTPUTS_FIELD, TOOL_FIELD, DRYRUN_FIELD, 'Test')))}):", ln)
    )
    return any(re.search(p, body) for p in PER_FILE_PHRASES)


def per_file_problems(d, tid) -> list[str]:
    """Problems for one task file: a 'decide per file' task with neither an `Outputs:` list nor a `Test:` line holding a per-file check phrase (PER_FILE_CHECK_PHRASES). The checklist alone does not prove each file was decided."""
    text = read_text(os.path.join(d, tid + ".md"))
    if not is_per_file_task(text) or _field(text, OUTPUTS_FIELD):
        return []
    test = _field(text, "Test").lower()
    if any(w in test for w in PER_FILE_CHECK_PHRASES):
        return []
    return [
        f"{tid}: decides per file but has no per-file acceptance: add `Outputs:` (one expected file or count per input) or a `Test:` that fails while a file is undecided"
    ]


def dryrun_required(text) -> bool:
    """True when the task runs a tool over data: a `Tool:` line, or a script in Predicted paths used with a dry-run flag (DRYRUN_FLAGS) in its text."""
    if _field(text, TOOL_FIELD):
        return True
    return any(p.endswith(SCRIPT_EXTS) for p in paths_in(text)) and any(f in text for f in DRYRUN_FLAGS)


def dryrun_missing_problems(d, tid) -> list[str]:
    """Problems for one task file: it runs a tool over data (dryrun_required) but has no `Dry run: <cmd> # expect > N changes` line, so a no-op tool goes unnoticed."""
    text = read_text(os.path.join(d, tid + ".md"))
    if not dryrun_required(text):
        return []
    return (
        []
        if parse_dryrun(_field(text, DRYRUN_FIELD))
        else [
            f"{tid}: runs a tool over data but has no valid `{DRYRUN_FIELD}: <cmd>  # expect > N changes` line (`plan.py dryrun` proves the tool is not a no-op)"
        ]
    )


DRYRUN_LINE_RE = re.compile(r"^(?P<cmd>.+?)\s*#\s*expect\s*>\s*(?P<n>\d+)\s*changes?\s*$", re.I)


def parse_dryrun(value) -> tuple[str, int] | None:
    """-> (cmd, n) from `cmd  # expect > n changes`, None when malformed."""
    m = DRYRUN_LINE_RE.match(value or "")
    return (m["cmd"], int(m["n"])) if m else None
