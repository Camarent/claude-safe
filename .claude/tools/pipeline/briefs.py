"""Task briefs: the dev and reviewer prompts, code leads and the script-driven switches they read."""

import ast
import fnmatch
import hashlib
import os
import re
import shlex
import sys
from pathlib import Path

from . import gitx, linkguard, maincheck
from .configuration import (
    CLAUDE_FILE,
    DB_START_PREFIX,
    ENV_NO_LEADS,
    EV_DISPATCH,
    EV_REPORT,
    GATE_OUTPUT_CHARS,
    GATE_SKIP_PATHS,
    LEAD_NOTE_CHARS,
    LEAD_SLICE_CHARS,
    LEAD_SLICE_CHARS_WITH_PATTERN,
    LOCAL_FIND_TIMEOUT_S,
    MAIN_DIRT_REWORK_NOTE,
    PRACTICES_DIR,
    PRE_EXISTING_RED,
    QRUN_CHAIN_READ_ONLY,
    QRUN_WRAPPER,
    REVIEW_DOCS_MAX_TURNS,
    SCOPE_HINT_NOTE,
    SCRATCH_DIR,
    SERVE_DIR_NAME,
    TASKS_FILE,
    WORK_DIR,
    driver_gates_on,
    gates_of,
    project,
    tool_cmd,
    work_branch,
)
from .plan_check import lead_verified_lines
from .plan_files import paths_in, task_affected_tests
from .scopeguard import scope_note
from .util import TOOLS, read_text, run_py, swallowed, work_path, write_text
from .ux_flows import flows_rel, variables
from .verdicts import ROLES
from .worktrees import job_diff, job_info, review_note

BASH_NOTE = (  # every role: the Bash rules that cmd_guard denies when broken
    " One Bash command per call. `git -C <dir>` never `cd <dir>`: use `git -C <dir> <args>` (plain `git <args>` when your cwd is already the worktree), e.g. `git -C wt status`."
    f" One `{QRUN_WRAPPER}` call per Bash call: after it only `&& <another {QRUN_WRAPPER} call>` or `&& <read-only command>` ({', '.join(QRUN_CHAIN_READ_ONLY)}), e.g. `{QRUN_WRAPPER} -- pytest && ls out`."
    " No `;`, `&&`, `|` chains outside the qrun form: a pipe goes into a script file."
    " Model set on every Agent call."
    " Read JSON/JSONL with `jq` (allowed, no approval), not python/node scripts: `jq -c 'select(.id==\"sd-170\")' f.jsonl`, `jq '.items|length' f.json`, `jq -r '.items|to_entries[]|[.key,.value.ok,.value.bad]|@tsv' f.json`; recipes: practices/bash.md."
)
NAV = (
    f' First search for code with `lp find "question"` or `lp nav def|refs|outline FILE`; grep only to confirm literal text afterwards. Offload to the local model (no paid tokens): `lp judge|review` for criteria and diff checks, `lp bulk|pack` for long reads (see {PRACTICES_DIR}/code-nav.md).'
    + BASH_NOTE
)


def tail(a, rid, allowed, what="your full report") -> str:
    return (
        f" Scratchpad for temp files and logs: {WORK_DIR}/{a.stage}/{SCRATCH_DIR}/ (write nothing temporary elsewhere; delete what you no longer need when you finish)."
        f" Write {what} to .work/{a.stage}/reports/report-{rid}.md, its FIRST line `VERDICT: {allowed}`, then reply with exactly: REPORT {rid}"
    )


UNVERIFIED_RULE = " If the app cannot be served or an item cannot be verified, add a line `UNVERIFIED: <items>` (the verdict then becomes BLOCKED, never PASS)."


def serve_dir(a) -> str:
    """The stage's servectl state dir (pid files, logs) under its scratchpad: the web-tester passes it to servectl, the driver stops from it."""
    return os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage, SCRATCH_DIR, SERVE_DIR_NAME)


def webtest_build_note(cfg) -> str:
    """Brief text for pipeline.json `webtest_build` (empty when unset): build fresh first so a stale build is never tested."""
    cmd = cfg.get("webtest_build")
    if not cmd:
        return ""
    return f" First run `{cmd}` on this branch checkout and serve only that fresh build, never an older build output; put the checked-out commit (`git rev-parse --short HEAD`) in your report."


def head_note(a) -> str:
    """Brief text: the branch HEAD sha (read through gitx) and the rule to put it as a `HEAD: <sha>` line first in the report; a note saying so when git cannot read HEAD."""
    sha = gitx.try_text(os.path.abspath(a.dir), "rev-parse", "HEAD").strip()
    if not sha:
        return " The branch HEAD could not be read: say so in your report and write no HEAD line."
    return f" The branch HEAD is {sha}: put a line HEAD: {sha} as the first line of your report."


def ux_flows_note(a, cfg) -> str:
    """Brief text for pipeline.json `ux_flows` (empty when unset): the project's named flows (path filled from the stage's UX-VARS), with the rule to run those that cover the changed files and to report the changed UI files no flow covers."""
    path = flows_rel(cfg, variables(read_text(os.path.join(a.dir, WORK_DIR, a.stage, TASKS_FILE))))
    if not path:
        return ""
    return (
        f" The project's named UI flows are in `{path}` (under the repo root; JSON list of {{name, actions, covers}}; `covers` are globs of the UI source files a flow shows; format in ux_shots.js)."
        " Run every flow whose `covers` match a UI file the stage changed (and every flow when none match). A changed UI file that no flow covers is UNVERIFIED: name it and the flow to add."
    )


def webtest_prompt(a, task, rid) -> str:
    """The brief of a `Type: webtest` task for the web-tester agent: the task file is the checklist, the report file it names is the deliverable."""
    t = os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage, f"{task['id']}.md")
    return (
        f"Stage {a.stage}: run the browser check of the task in {t} (read it there; index .work/{a.stage}/TASKS.md). Do its Goal and Steps; write the report file the task names."
        f" Serve only with servectl and this state dir: `--state-dir {shlex.quote(serve_dir(a))}`."
        + webtest_build_note(project(getattr(a, "dir", ".")))
        + head_note(a)
        + UNVERIFIED_RULE
        + tail(a, rid, "|".join(ROLES["web-tester"][0]), "your full report")
    )


def research_prompt(a, task, rid) -> str:
    """The brief of a `Type: research` task for the scout (read-only, no Write tool): the task file is the checklist; the scout's reply is the report, the lead saves it."""
    t = os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage, f"{task['id']}.md")
    return (
        f"Stage {a.stage}: answer the research question of the task in {t} (read it there; index .work/{a.stage}/TASKS.md). Do its Goal and Steps; read the tree only, never edit or delete."
        " Your reply is the document the task names, not a file: you have no Write tool, so the lead saves your reply as the report."
        " The first line of your reply is `VERDICT: DONE|BLOCKED` (BLOCKED when the question cannot be answered from the tree; then say why in the body), then the document body."
        f" Scratchpad for temp files: {WORK_DIR}/{a.stage}/{SCRATCH_DIR}/ (write nothing temporary elsewhere). Report id: {rid}."
    )


def extra_leads(d, text) -> str:
    """Exact, no-model parts for a brief: where the names marked with backticks are defined, and the outline of each file under Predicted paths. "" when lp has nothing."""
    try:
        sys.path.insert(0, TOOLS)
        from search.leadparts import outline_lines, symbol_lines

        parts = symbol_lines(d, text, 6) + outline_lines(d, paths_in(text)[:4], 12)
        return "\n" + "\n".join(parts)[:1500] if parts else ""
    except Exception as ex:
        swallowed("steps.extra_leads", ex)
        return ""


ENV_DRY_NO_CCN = "PIPELINE_DRY_NO_CCN"  # set by the dry-run walker: no complexity gate (no worktrees there)
LEAD_RE = re.compile(r"^\s*(\S+?):(\d+)(?:-(\d+))?(?=\s|$)")


def _lead_lines(root, path, start, end, cap):
    """Numbered source lines START..END (capped) of `path` under `root`; None when not a file inside root."""
    full = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
        return None
    lines = Path(full).read_text(encoding="utf-8", errors="replace").splitlines()
    hi = min(end, start + cap - 1, len(lines))
    if start < 1 or start > hi:
        return None
    return [f"{n}: {lines[n - 1]}" for n in range(start, hi + 1)]


PREDICTED_RE = re.compile(r"(?m)^Predicted paths:[ \t]*\S")


def slice_chars(text) -> int:
    """Char cap of the code slices under the leads: the smaller one when the task names a Pattern file and its Predicted paths, else the larger one."""
    return LEAD_SLICE_CHARS_WITH_PATTERN if PATTERN_RE.search(text) and PREDICTED_RE.search(text) else LEAD_SLICE_CHARS


def _unique_leads(leads_text):
    """-> (path, start, end) of each `path:START-END` lead, in order, without repeats or reversed ranges."""
    seen: set = set()
    for line in leads_text.splitlines():
        m = LEAD_RE.match(line)
        if not m:
            continue
        path, start = m.group(1), int(m.group(2))
        end = int(m.group(3) or start)
        if end < start or (path, start, end) in seen:
            continue
        seen.add((path, start, end))
        yield path, start, end


def _lead_body(root, path, start, end, cap):
    """The code lines of one lead, or None when the file cannot be read."""
    try:
        return _lead_lines(root, path, start, end, cap)
    except (OSError, ValueError):
        return None


def _fit_block(block, out, max_chars):
    """-> (the block cut to the room left after `out`, or None when no room; True when the block was cut)."""
    room = max_chars - len("\n".join(out)) - (1 if out else 0)
    if room <= 0:
        return None, True
    if len(block) <= room:
        return block, False
    cut = block[:room].rsplit("\n", 1)[0]
    return (cut, True) if "\n" in cut else (None, True)


def lead_slices(d, leads_text, max_slices=4, cap=40, max_chars=LEAD_SLICE_CHARS) -> str:
    """Real code lines under the `path:START-END` leads. Read fresh on every call (no cache). "" when nothing. Never raises."""
    try:
        root = os.path.realpath(d)
        out: list = []
        for path, start, end in _unique_leads(leads_text):
            if len(out) >= max_slices:
                break
            body = _lead_body(root, path, start, end, cap)
            if not body:
                continue
            block, cut = _fit_block("\n".join([f"{path}:{start}-{end}", *body]), out, max_chars)
            if block is not None:
                out.append(block)
            if cut:
                break
        return "\n".join(out)
    except Exception as ex:
        swallowed("steps.lead_slices", ex)
        return ""


def _led_files(root, leads_text, text) -> list[str]:
    """The existing files the leads and the task text name, first six, without repeats."""
    led = [m[1] for m in (LEAD_RE.match(line) for line in leads_text.splitlines()) if m]
    cands = [*led, *paths_in(text)[:4]]
    files: list[str] = []
    for p in cands:
        if p not in files and os.path.isfile(os.path.join(root, p)):
            files.append(p)
    return files[:6]


def related_files(d, leads_text, text) -> str:
    """One line naming the docs and tests of the led files (names only, no model). "" when none. Never raises."""
    try:
        sys.path.insert(0, TOOLS)
        from search.leads_docs import with_docs, with_tests

        root = os.path.realpath(d)
        files = _led_files(root, leads_text, text)
        if not files:
            return ""
        extra = [p for p in with_tests(root, with_docs(root, files)) if p not in files][:8]
        if not extra:
            return ""
        return f" Related docs and tests of these files (names only; open only if needed): {', '.join(extra)}.\n"
    except Exception as ex:
        swallowed("steps.related_files", ex)
        return ""


def _drop_stale_leads(cache, stamp, digest):
    """Delete the cached leads and their -rel file when the stamp holds another text digest."""
    if os.path.exists(cache) and os.path.exists(stamp) and Path(stamp).read_text(encoding="utf-8").strip() != digest:
        # the text changed (a retry edited task-N.md): the cached leads are for the old text
        for stale in (cache, f"{cache[: -len('.md')]}-rel.md"):
            if os.path.exists(stale):
                os.remove(stale)


def _search_leads(d, text):
    """Output of `local.py find` on TEXT (first 300 chars, 1500 kept) plus extra leads; empty when the search fails."""
    try:
        q = " ".join(text.split())[:300]
        r = run_py("search/local.py", "find", q, cwd=d, timeout=LOCAL_FIND_TIMEOUT_S)
        out = r.stdout.strip()[:1500] if r.returncode == 0 else ""
    except Exception as ex:
        swallowed("steps.leads_for", ex)
        out = ""
    return (out + extra_leads(d, text)).strip()


def leads_for(a, key, text) -> str:
    """Mandatory first code search: local.py find on `text`, cached in .work/<stage>/leads/leads-<key>.md and put in the brief. Never blocks."""
    if os.environ.get(ENV_NO_LEADS):  # tests
        return ""
    d = getattr(a, "dir", ".")
    cache = work_path(os.path.join(d, WORK_DIR, a.stage), f"leads-{key}.md", create=True)
    stamp = work_path(os.path.join(d, WORK_DIR, a.stage), f"leads-{key}.hash", create=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    _drop_stale_leads(cache, stamp, digest)
    if os.path.exists(cache):
        out = Path(cache).read_text(encoding="utf-8").strip()
    else:
        out = _search_leads(d, text)
        if out:  # an empty result (search failed, model down) is not cached: the next call searches again
            write_text(cache, out)
            write_text(stamp, digest)
    if not out or "IDK" in out[:20]:
        return ""
    text_out = f" Code leads from `local.py find` (start here, then read only what you need):\n{out}\n"
    slices = lead_slices(d, out, max_chars=slice_chars(text))
    if slices:
        text_out += f" Code of the leads (read it here, open the file only for more):\n{slices}\n"
    rel_cache = work_path(os.path.join(d, WORK_DIR, a.stage), f"leads-{key}-rel.md", create=True)
    if os.path.exists(rel_cache):
        rel = Path(rel_cache).read_text(encoding="utf-8").strip()
    else:
        rel = related_files(d, out, text).strip()
        if rel:  # no empty files: a missing -rel file reads as empty, so an empty result is not written
            write_text(rel_cache, rel)
    if rel:
        text_out += f" {rel}\n"
    return text_out


def read_head(a, name, n=600) -> str:
    try:
        return Path(os.path.join(getattr(a, "dir", "."), WORK_DIR, a.stage, name)).read_text(encoding="utf-8")[:n]
    except OSError:
        return ""


def leads(a, task) -> str:
    return leads_for(a, task["id"], read_head(a, f"{task['id']}.md", 3000))


def _where(a, task):
    """Dev brief sentence on where to work: the script-made job worktrees when `worktrees` is on in pipeline.json, else in place."""
    try:
        info = job_info(a, task["id"], create=True)
    except RuntimeError as e:  # no fallback to the main checkout: the dev stops and the wave hands the task back to the lead
        return f"The job worktree could not be created: {str(e)[:400]}. Edit NOTHING. Reply at once with VERDICT: BLOCKED and that reason."
    if info:
        maincheck.snapshot(getattr(a, "dir", "."), a.stage, task["id"])  # a dev that writes to the main checkout is named after its run
        linkguard.snapshot(getattr(a, "dir", "."), a.stage, task["id"])  # the dev starts now: what its linked repos hold is not its doing
    if not info:
        return f"Work in place on `{work_branch(project(getattr(a, 'dir', '.')), getattr(a, 'feature', None) or getattr(a, 'stage', ''))}` (no job branch) unless the task file names a branch; commit only after the tests pass."
    paths = "; ".join(f"`{p}`" + ("" if r == next(iter(info["repos"])) else f" (repo {r})") for r, p in info["repos"].items())
    first = next(iter(info["repos"].values()))
    return (
        f"Work ONLY in these job worktrees, already created on branch `{info['branch']}`: {paths}. Never edit the main checkouts: edit files only with paths relative to your worktree or absolute paths under it, never a path into the main checkout. Run every command from `{first}` "
        f"(paths in the task file are relative to it; a repo listed after it sits inside it at its relative path). After the tests pass, commit ALL your work with one command (it commits in every worktree you changed, nested repos too): "
        f'`{tool_cmd("wt.py")} commit --stage {a.stage} --task {task["id"]} --subject "<type>(<area>): <what>" --dir {os.path.abspath(getattr(a, "dir", "."))}` '
        f"(type feat|fix|test|docs|refactor|chore; one line). Do not run `git commit` yourself. Do not merge, do not remove worktrees. "
        f"A nested repo not listed above is a read-only link: never edit through it; if the task needs to change one, first run "
        f"`{tool_cmd('wt.py')} add-repo --stage {a.stage} --task {task['id']} --repo <nested repo path> --dir {os.path.abspath(getattr(a, 'dir', '.'))}`. "
        f"A gate that is red on the base too is not yours: report `{PRE_EXISTING_RED}` and finish DONE."
    )


def _lint_left(a, task):
    """One line for the reviewer brief: findings the quality run still reports on the changed lines (the dev may have skipped some). Count only; the
    reviewer neither lists nor judges them. Any error or a clean run -> ''."""
    if not qualityrun_on(a):
        return ""
    try:
        if TOOLS not in sys.path:
            sys.path.insert(0, TOOLS)
        from audit import qualityrun

        v, text = qualityrun.run(a.stage, task["id"], os.path.abspath(getattr(a, "dir", ".")))
    except Exception as ex:  # advisory line only; never block a review
        swallowed("steps.lint_line", ex)
        return ""
    n = sum(int(m) for m in re.findall(r"(\d+) finding\(s\)", text)) if v == "FAIL" else 0
    return (
        f". Driver lint left: {n} finding(s) on the changed lines (script quality run; count only, do not list or judge them, state the count in your reply)"
        if n
        else ""
    )


def docs_only(diff) -> bool:
    """True when every file in the diff matches a GATE_SKIP_PATHS glob (docs only). False for an empty or unparsed diff."""
    names = re.findall(r"(?m)^diff --git a/(\S+) b/", diff or "")
    return bool(names) and all(any(fnmatch.fnmatch(n, g) for g in GATE_SKIP_PATHS) for n in names)


def _test_run_ids(evs) -> set:
    """Ids of the driver test-run script dispatches among the events."""
    return {e["id"] for e in evs if e["ev"] == EV_DISPATCH and e.get("role") == "script" and (e.get("why") or "").startswith("test run")}


def gate_output(a, task) -> str:
    """Tail of the report of the task's last driver test run ("" when none ran or the file is gone): the reviewer reads it in the brief instead of rerunning the gate."""
    evs = [e for e in (getattr(a, "orch_events", None) or []) if e.get("task") == task["id"]]
    ids = _test_run_ids(evs)
    rep = next((e for e in reversed(evs) if e["ev"] == EV_REPORT and e.get("id") in ids and e.get("file")), None)
    return read_text(rep["file"]).strip()[-GATE_OUTPUT_CHARS:] if rep else ""


def _review_base(a, task):
    info = job_info(a, task["id"], create=False)
    if not info:
        return "git diff vs the stage base = the commit before this stage's first task"
    d = job_diff(a, task["id"])
    head = f"the work is committed on branch `{info['branch']}` (worktrees: {', '.join(info['repos'].values())}); full diff per repo: `git diff {info['base']}...{info['branch']}`; leave the worktrees as they are"
    ran = (
        ". Driver test run: the script already ran the task's Test lines in the worktree after the dev's last DONE and they passed (a task with no Test line was not run); do not rerun them or the gate"
        if testrun_on(a)
        else ""
    )
    out = gate_output(a, task) if testrun_on(a) else ""
    green = f". Green gate output (tail, from the driver run; the gate passed):\n{out}\n" if out else ""
    docs = f". Docs-only diff: review in at most {REVIEW_DOCS_MAX_TURNS} turns" if docs_only(d) else ""
    return (
        head
        + ran
        + _lint_left(a, task)
        + green
        + docs
        + (f". The change as the script saw it (long diffs are cut, read the rest with the command above):\n{d}\n" if d else "")
    )


NO_CHECKOUT_NOTE = (
    " A retry after BLOCKED starts from a clean worktree: do not build on files an earlier try left, redo the task from the committed state."
    " Never run `git checkout --`, `git restore` or `git reset`."
)

# dev brief: a file outside the predicted paths is allowed when a red gate or the Test line needs it
DEV_SCOPE_NOTE = (
    " Predicted paths are a hint, not a limit: when a red gate or your Test line needs another file, edit it and keep it;"
    " never revert a correct fix to fit the scope. The driver records extra files by script."
)


# dev brief: the job worktree root is the cwd; every path the dev writes stays relative to it
WORKTREE_NOTE = " Your job worktree root is your current directory: edit files only with paths relative to it or absolute paths under it; never a path into the main checkout."

PATTERN_RE = re.compile(r"(?m)^Pattern:[ \t]*([^\s,:]+)")


def pattern_header_note(root, task_file) -> str:
    """Dev brief lines with the import and sys.path header of the task's Pattern file when that file is a test file, else ''."""
    m = PATTERN_RE.search(read_text(task_file))
    rel = m[1] if m else ""
    name = os.path.basename(rel)
    if not (name.startswith("test_") and name.endswith(".py")):
        return ""
    base = os.path.realpath(root)
    try:
        full = os.path.realpath(os.path.join(base, rel))
        if os.path.commonpath([base, full]) != base:  # an absolute or `../` Pattern path never reads outside the repo
            return ""
        src = read_text(full)
        tree = ast.parse(src)
    except (ValueError, OSError, SyntaxError):  # bad path text (NUL byte), unreadable or broken pattern file: no header, the dev still reads the file itself
        return ""
    lines = src.splitlines()
    stmts = [
        "\n".join(lines[node.lineno - 1 : node.end_lineno])
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func).startswith("sys.path"))
    ]
    if not stmts:
        return ""
    return (
        f" The Pattern test file {rel} starts with these import and sys.path lines (copy them into any new test file of the same kind):\n"
        + "\n".join(stmts)
        + "\n"
    )


TASK_GATES_NOTE = (
    " Run your task's own `Test:` lines and the tests of the code you touch while you work; if you know of tests elsewhere that your change could affect, run those too."
    " Do not run the full suite: the driver runs the full gate set after you finish and sends any failure back to you with its output."
)


BUILD_FIRST_NOTE = " For reference, the gates the driver runs: {names}. When your change touches source that a build gate compiles into an artifact, run only the build part of that gate before any test that loads the artifact: a test run on a stale build fails for no reason of yours. Do not run the other gates."


def gates_note(a) -> str:
    """Dev brief text naming the project's gates from pipeline.json (empty when none): run each before DONE, report a `Gates:` line."""
    cfg = project(getattr(a, "dir", "."))
    gates = gates_of(cfg)
    if not gates:
        return ""
    names = "; ".join(f"{g['name']}: `{g['cmd']}`" for g in gates)
    if driver_gates_on(cfg):
        return TASK_GATES_NOTE + BUILD_FIRST_NOTE.format(names=names)
    return f" Before DONE run the full gate set, every gate below and none skipped silently ({names}); your report carries one `Gates:` line with each gate RAN or SKIPPED and its result. A negated test uses `! grep`."


AFFECTED_DEV = " Affected tests (hint, may be incomplete): {named}. Check them first; update them if the new behavior is intended."
AFFECTED_REVIEW = " Check that test changes match the task's Done and do not weaken a check."


def lead_note(task_file) -> str:
    """Brief sentence for a task file with lead-verified Done or Test lines, else ''. The lines are not named: the dev skips them, the driver runs them."""
    n = len(lead_verified_lines(read_text(task_file)))
    return f" {n} line(s) of the task file are marked lead-verified: not yours to run or to count as criteria; the driver runs them after you." if n else ""


def affected_note(task_file, text) -> str:
    """Brief sentence for a task file with an `Affected tests:` line, else ''. TEXT is the sentence template."""
    named = task_affected_tests(Path(task_file).read_text(encoding="utf-8", errors="replace")) if os.path.isfile(task_file) else ""
    return text.format(named=named) if named else ""


def last_report_file(evs, tid) -> str:
    """Path of the task's newest report file that still exists, "" when none."""
    for e in reversed([e for e in evs if e.get("task") == tid and e["ev"] == EV_REPORT]):
        if e.get("file") and os.path.isfile(e["file"]):
            return e["file"]
    return ""


def lead_note_line(note, report_file) -> str:
    """The line in front of a lead-retry brief. With an existing report the agent gets its path (the findings stay there, not pasted) and the first line of the note."""
    if not report_file:
        return f"Lead note (read first): {note}"
    return f"Lead note (read first): the findings of the last try are in {report_file}; read that file, they are not pasted here. {note.splitlines()[0][:LEAD_NOTE_CHARS]}"


def rework_note(rework_file, main_dirt=False) -> str:
    """Dev brief lines for a rework: the findings of the rework file verbatim, with the rule to fix all of them in one pass. "" without a rework file.
    MAIN_DIRT (main_dirt True) adds the worktree-relative path rule."""
    if not rework_file:
        return ""
    dirt_line = MAIN_DIRT_REWORK_NOTE if main_dirt else ""
    if not os.path.isfile(rework_file):  # the file is gone: the path alone still names the findings
        return f" Rework: fix the findings in {rework_file} first. Fix ALL of them in one pass.{dirt_line}"
    return f" Rework: fix the findings in {rework_file} first. Fix ALL of them in one pass; they are verbatim below:\n{read_text(rework_file)}\n{dirt_line}"


def _main_dirt_report(rework_file):
    """True when the rework file holds a MAIN-DIRT note (the main checkout changed while the task ran)."""
    return bool(rework_file) and os.path.isfile(rework_file) and any(maincheck.dirt(line) for line in read_text(rework_file).splitlines())


def db_start_note(a) -> str:
    """Dev brief line `DB start: <cmd>` from the `db-start:` line of the workspace CLAUDE.md, "" when there is none."""
    for line in read_text(os.path.join(getattr(a, "dir", "."), CLAUDE_FILE)).splitlines():
        if line.startswith(DB_START_PREFIX):
            return f"\nDB start: {line[len(DB_START_PREFIX) :].strip()}"
    return ""


TOOLING_BLOCKED_NOTE = " A BLOCKED on a tooling failure names the cause: the tool, the command and the error line."
COMPLEXITY_RE = re.compile(r"\bCCN\b")
COMPLEXITY_NOTE = " Your task's Goal or Done mentions CCN: never run `cs-audit`; the gates run it."


def complexity_note(task_file) -> str:
    """Dev brief line forbidding `cs-audit` when the task's Goal: or Done: line mentions CCN, else ''."""
    lines = read_text(task_file).splitlines()
    return COMPLEXITY_NOTE if any(COMPLEXITY_RE.search(line) for line in lines if line.startswith(("Goal:", "Done:"))) else ""


REVIEW_INDEX_NOTE = " The generated docs INDEX.md diff from wt.py commit is expected: never a finding, never out of scope."
DEV_CITE_NOTE = " Never cite line numbers of a file this task splits or moves code out of: cite the symbol."
LIZARD_NOTE = (
    " The limits come from AUDIT.md and the gates run lizard: on a retry after a red lizard gate split the function, do not raise a limit;"
    " edit AUDIT.md only when the task lists it."
)
CI_DRIFT_NOTE = (
    " The task mentions ci_drift_ignore: change only the ci_drift_ignore list in pipeline.json, one entry as the pr-ready message hints, never a gate."
)
PUBLIC_WORDS_NOTE = (
    " The task mentions public-words: hits that match the baseline file are old; skip them and write one line"
    " 'old public-words hits skipped: <count from the scan>' in the report; fix only new hits."
)


def _mentions(paths, words) -> bool:
    """True when any existing file in paths contains one of words (case-sensitive)."""
    return any(any(w in read_text(p) for w in words) for p in paths if p and os.path.isfile(p))


def dev_notes(task_file, rework_file) -> str:
    """Dev brief notes that depend on the task text: lizard (task or rework), ci_drift_ignore and public-words (task only)."""
    return (
        (LIZARD_NOTE if _mentions([task_file, rework_file], ("lizard", "AUDIT.md")) else "")
        + (CI_DRIFT_NOTE if _mentions([task_file], ("ci_drift_ignore",)) else "")
        + (PUBLIC_WORDS_NOTE if _mentions([task_file], ("public-words", "public words")) else "")
    )


def prompt_for(a, task, role, rework_file, rid) -> str:
    t = os.path.join(os.path.abspath(getattr(a, "dir", ".")), WORK_DIR, a.stage, f"{task['id']}.md")
    out = (
        NAV
        + leads(a, task)
        + tail(
            a, rid, "|".join(ROLES[role][0]), "your full report (files changed, test result, any ESCALATE line; for a review: counts and every 'wrong' item)"
        )
    )
    if role == "dev":
        extra = (
            rework_note(rework_file, _main_dirt_report(rework_file))
            + NO_CHECKOUT_NOTE
            + DEV_SCOPE_NOTE
            + WORKTREE_NOTE
            + pattern_header_note(os.path.abspath(getattr(a, "dir", ".")), t)
            + gates_note(a)
            + db_start_note(a)
            + TOOLING_BLOCKED_NOTE
            + complexity_note(t)
            + DEV_CITE_NOTE
            + dev_notes(t, rework_file)
            + affected_note(t, AFFECTED_DEV)
            + lead_note(t)
        )
        return f"Stage {a.stage}: take the task in {t} (read it there, not in the worktree; index .work/{a.stage}/TASKS.md).{extra} Mark nothing yourself. {_where(a, task)}{out}"
    return f"Review the result of the task in {t} against its done criteria ({_review_base(a, task)}). Before a FAIL on a 'still present' claim, quote the exact line from the current file.{REVIEW_INDEX_NOTE}{affected_note(t, AFFECTED_REVIEW)}{SCOPE_HINT_NOTE}{scope_note(a, task['id'])}{review_note(a, task['id'])}{out}"


def plan_fix_prompt(a, task, rid, why, report) -> str:
    """Architect brief for a reviewer PLAN-ERROR: correct the task file, never the code."""
    t = os.path.join(os.path.abspath(getattr(a, "dir", ".")), WORK_DIR, a.stage, f"{task['id']}.md")
    return (
        f"Stage {a.stage}: the reviewer found a plan error in the task file {t} (the dev's code is not at fault): {why} Review report: {report}. "
        "Edit ONLY that task file so its Done/Test lines match the code the diff's routes and callers actually use; touch no other task, no TASKS.md row, no code. "
        "If the report shows no plan error, change nothing and reply BLOCKED."
        + tail(a, rid, "|".join(ROLES["architect"][0][:2]), "a 3-line summary of the change to the task file")
    )


def testrun_on(a) -> bool:
    return bool(project(getattr(a, "dir", ".")).get("testrun", True))


def qualityrun_on(a) -> bool:
    return bool(project(getattr(a, "dir", ".")).get("qualityrun", True))
