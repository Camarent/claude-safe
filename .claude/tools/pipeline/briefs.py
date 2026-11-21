"""Task briefs: the dev and reviewer prompts, code leads and the script-driven switches they read."""

import hashlib
import os
import re
import shlex
import sys
from pathlib import Path

from . import linkguard, maincheck
from .configuration import (
    ENV_NO_LEADS,
    LOCAL_FIND_TIMEOUT_S,
    PRACTICES_DIR,
    PRE_EXISTING_RED,
    QRUN_CHAIN_READ_ONLY,
    QRUN_WRAPPER,
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
from .plan_files import paths_in, task_affected_tests
from .util import TOOLS, read_text, run_py, swallowed, work_path, write_text
from .ux_flows import flows_rel, variables
from .verdicts import ROLES
from .scopeguard import scope_note
from .worktrees import job_diff, job_info, review_note

BASH_NOTE = (  # every role: the two Bash rules that cmd_guard denies when broken
    " Never `cd <dir>` before git: use `git -C <dir> <args>` (plain `git <args>` when your cwd is already the worktree), e.g. `git -C wt status`."
    f" One `{QRUN_WRAPPER}` call per Bash call: after it only `&& <another {QRUN_WRAPPER} call>` or `&& <read-only command>` ({', '.join(QRUN_CHAIN_READ_ONLY)}), e.g. `{QRUN_WRAPPER} -- pytest && ls out`."
    " Read JSON/JSONL with `jq` (allowed, no approval), not python/node scripts: `jq -c 'select(.id==\"sd-170\")' f.jsonl`, `jq '.items|length' f.json`, `jq -r '.items|to_entries[]|[.key,.value.ok,.value.bad]|@tsv' f.json`; recipes: practices/bash.md."
)
NAV = (
    f' First search for code with `lp find "question"` or `lp nav def|refs|outline FILE`; grep only to confirm literal text afterwards. Offload to the local model (no paid tokens): `lp judge|review` for criteria and diff checks, `lp bulk|pack` for long reads (see {PRACTICES_DIR}/code-nav.md).'
    + BASH_NOTE
)


def tail(a, rid, allowed, what="your full report"):
    return (
        f" Scratchpad for temp files and logs: {WORK_DIR}/{a.stage}/{SCRATCH_DIR}/ (write nothing temporary elsewhere; delete what you no longer need when you finish)."
        f" Write {what} to .work/{a.stage}/reports/report-{rid}.md, its FIRST line `VERDICT: {allowed}`, then reply with exactly: REPORT {rid}"
    )


UNVERIFIED_RULE = " If the app cannot be served or an item cannot be verified, add a line `UNVERIFIED: <items>` (the verdict then becomes BLOCKED, never PASS)."


def serve_dir(a):
    """The stage's servectl state dir (pid files, logs) under its scratchpad: the web-tester passes it to servectl, the driver stops from it."""
    return os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage, SCRATCH_DIR, SERVE_DIR_NAME)


def webtest_build_note(cfg):
    """Brief text for pipeline.json `webtest_build` (empty when unset): build fresh first so a stale build is never tested."""
    cmd = cfg.get("webtest_build")
    if not cmd:
        return ""
    return f" First run `{cmd}` on this branch checkout and serve only that fresh build, never an older build output; put the checked-out commit (`git rev-parse --short HEAD`) in your report."


def ux_flows_note(a, cfg):
    """Brief text for pipeline.json `ux_flows` (empty when unset): the project's named flows (path filled from the stage's UX-VARS), with the rule to run those that cover the changed files and to report the changed UI files no flow covers."""
    path = flows_rel(cfg, variables(read_text(os.path.join(a.dir, WORK_DIR, a.stage, TASKS_FILE))))
    if not path:
        return ""
    return (
        f" The project's named UI flows are in `{path}` (under the repo root; JSON list of {{name, actions, covers}}; `covers` are globs of the UI source files a flow shows; format in ux_shots.js)."
        " Run every flow whose `covers` match a UI file the stage changed (and every flow when none match). A changed UI file that no flow covers is UNVERIFIED: name it and the flow to add."
    )


def webtest_prompt(a, task, rid):
    """The brief of a `Type: webtest` task for the web-tester agent: the task file is the checklist, the report file it names is the deliverable."""
    t = os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage, f"{task['id']}.md")
    return (
        f"Stage {a.stage}: run the browser check of the task in {t} (read it there; index .work/{a.stage}/TASKS.md). Do its Goal and Steps; write the report file the task names."
        f" Serve only with servectl and this state dir: `--state-dir {shlex.quote(serve_dir(a))}`."
        + webtest_build_note(project(getattr(a, "dir", ".")))
        + UNVERIFIED_RULE
        + tail(a, rid, "|".join(ROLES["web-tester"][0]), "your full report")
    )


def extra_leads(d, text):
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


def lead_slices(d, leads_text, max_slices=4, cap=40, max_chars=6000):
    """Real code lines under the `path:START-END` leads. Read fresh on every call (no cache). "" when nothing. Never raises."""
    try:
        root = os.path.realpath(d)
        out: list = []
        seen: set = set()
        for line in leads_text.splitlines():
            if len(out) >= max_slices:
                break
            m = LEAD_RE.match(line)
            if not m:
                continue
            path, start = m.group(1), int(m.group(2))
            end = int(m.group(3) or start)
            if end < start or (path, start, end) in seen:
                continue
            seen.add((path, start, end))
            try:
                body = _lead_lines(root, path, start, end, cap)
            except (OSError, ValueError):
                body = None
            if not body:
                continue
            block = "\n".join([f"{path}:{start}-{end}", *body])
            joined = "\n".join(out)
            room = max_chars - len(joined) - (1 if out else 0)
            if room <= 0:
                break
            if len(block) > room:
                block = block[:room].rsplit("\n", 1)[0]
                if "\n" not in block:
                    break
                out.append(block)
                break
            out.append(block)
        return "\n".join(out)
    except Exception as ex:
        swallowed("steps.lead_slices", ex)
        return ""


def related_files(d, leads_text, text):
    """One line naming the docs and tests of the led files (names only, no model). "" when none. Never raises."""
    try:
        sys.path.insert(0, TOOLS)
        from search.leads_docs import with_docs, with_tests

        root = os.path.realpath(d)
        files = []
        for line in leads_text.splitlines():
            m = LEAD_RE.match(line)
            if m and m.group(1) not in files and os.path.isfile(os.path.join(root, m.group(1))):
                files.append(m.group(1))
        for p in paths_in(text)[:4]:
            if p not in files and os.path.isfile(os.path.join(root, p)):
                files.append(p)
        files = files[:6]
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


def leads_for(a, key, text):
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
    slices = lead_slices(d, out)
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


def read_head(a, name, n=600):
    try:
        return Path(os.path.join(getattr(a, "dir", "."), WORK_DIR, a.stage, name)).read_text(encoding="utf-8")[:n]
    except OSError:
        return ""


def leads(a, task):
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
        f"Work ONLY in these job worktrees, already created on branch `{info['branch']}`: {paths}. Never edit the main checkouts: use paths relative to your worktree, never an absolute path into the main checkout. Run every command from `{first}` "
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


def _review_base(a, task):
    info = job_info(a, task["id"], create=False)
    if not info:
        return "git diff vs the stage base = the commit before this stage's first task"
    d = job_diff(a, task["id"])
    head = f"the work is committed on branch `{info['branch']}` (worktrees: {', '.join(info['repos'].values())}); full diff per repo: `git diff {info['base']}...{info['branch']}`; leave the worktrees as they are"
    ran = (
        ". Driver test run: the script already ran the task's Test lines in the worktree after the dev's last DONE and they passed (a task with no Test line was not run); do not rerun them"
        if testrun_on(a)
        else ""
    )
    return (
        head
        + ran
        + _lint_left(a, task)
        + (f". The change as the script saw it (long diffs are cut, read the rest with the command above):\n{d}\n" if d else "")
    )


NO_CHECKOUT_NOTE = (
    " A retry after BLOCKED starts from a clean worktree: do not build on files an earlier try left, redo the task from the committed state."
    " Never run `git checkout --`, `git restore` or `git reset`."
)


TASK_GATES_NOTE = (
    " Run your task's own `Test:` lines and the tests of the code you touch while you work; if you know of tests elsewhere that your change could affect, run those too."
    " Do not run the full suite: the driver runs the full gate set after you finish and sends any failure back to you with its output."
)


BUILD_FIRST_NOTE = " For reference, the gates the driver runs: {names}. When your change touches source that a build gate compiles into an artifact, run only the build part of that gate before any test that loads the artifact: a test run on a stale build fails for no reason of yours. Do not run the other gates."


def gates_note(a):
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


def affected_note(task_file, text):
    """Brief sentence for a task file with an `Affected tests:` line, else ''. TEXT is the sentence template."""
    named = task_affected_tests(Path(task_file).read_text(encoding="utf-8", errors="replace")) if os.path.isfile(task_file) else ""
    return text.format(named=named) if named else ""


def prompt_for(a, task, role, rework_file, rid):
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
            (f" Rework: fix the findings in {rework_file} first." if rework_file else "") + NO_CHECKOUT_NOTE + gates_note(a) + affected_note(t, AFFECTED_DEV)
        )
        return f"Stage {a.stage}: take the task in {t} (read it there, not in the worktree; index .work/{a.stage}/TASKS.md).{extra} Mark nothing yourself. {_where(a, task)}{out}"
    return f"Review the result of the task in {t} against its done criteria ({_review_base(a, task)}). Before a FAIL on a 'still present' claim, quote the exact line from the current file.{affected_note(t, AFFECTED_REVIEW)}{scope_note(a, task['id'])}{review_note(a, task['id'])}{out}"


def testrun_on(a):
    return bool(project(getattr(a, "dir", ".")).get("testrun", True))


def qualityrun_on(a):
    return bool(project(getattr(a, "dir", ".")).get("qualityrun", True))
