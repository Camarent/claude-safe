"""Phases after the planned tasks: wave merge prompt, tests, docs, audits, fix cycle, post-publish steps."""

import fnmatch
import json
import os
import re
import shlex
from collections.abc import Iterator
from pathlib import Path

from docs_index import module_headings, slug

from . import gitx, pr_mode
from .audit_phases import (
    _wb,
    audit_base,
    audit_dir_of,
    audit_rounds,
    docs_beside_audits,
    fix_step,
    last_report,
)
from .briefs import UNVERIFIED_RULE, head_note, serve_dir, tail, ux_flows_note, webtest_build_note
from .configuration import (
    ACCEPTANCE_STEP,
    BLOCKED,
    DOC_CITES_DEFAULT,
    DOC_CITES_FLAG,
    DOC_CITES_KEY,
    DOC_CITES_STEP,
    DONE,
    EV_DISPATCH,
    FAIL,
    FINAL_GATES_KEY,
    FINAL_TEST_RERUN_STEP,
    FINALTEST_TRIAGE_STEP,
    FIX_WEB_STEP,
    MERGE_GATES_KEY,
    MODEL_HAIKU,
    PASS,
    PLAN_FILE,
    PRACTICES_DIR,
    PREFLIGHT_MARKER,
    PUBLISH_BUILD,
    PUBLISH_CWD,
    PUBLISH_FILES,
    PUBLISH_KEY,
    PUBLISH_LINE_RE,
    PUBLISH_OUT,
    PUBLISH_PAGE,
    PUBLISH_RE,
    SCRATCH_DIR,
    STARTED_EVENTS,
    TASKS_FILE,
    WEBTEST_AGAIN_SUFFIX,
    WEBTEST_BATCH,
    WEBTEST_BATCH_SUFFIX,
    WEBTEST_EFFORT,
    WEBTEST_FIX_SUFFIX,
    WEBTEST_STEP,
    WEBTEST_WIDTHS,
    WORK_DIR,
    allowed,
    gates_of,
    merge_gates_of,
    project,
    repo_dir,
    repo_path,
    tool_cmd,
    ui_paths,
    webtest_model,
    webtest_tools_missing,
)
from .entry import name_of_stage
from .phase_audit import DEFAULT_EVERY, audit_due
from .plan_files import acceptance_items, is_finish_phase, task_paths
from .steps import history, last_verdict, ops_as_script, step
from .util import paths


def test_env_hint(cfg) -> str:
    """Instruction text for the env vars the project's test commands need ("" when none)."""
    env = cfg.get("test_env") or {}
    return "qrun sets " + " ".join(f"{k}" for k in env) + " itself (no prefix, no export), " if env else ""


def gate_text(cfg, merge=False) -> str:
    """-> the test commands for a prompt: the gates as `name: cmd` (with MERGE the `gates_merge` ones too), or a generic phrase without gates."""
    gates = [*gates_of(cfg), *merge_gates_of(cfg)] if merge else cfg.get("gates") or []
    return "; ".join(f"{g['name']}: `{g['cmd']}`" for g in gates) if gates else "the project test command"


def merge_prompt(a, what, parts) -> str:
    cfg = project(a.dir)
    return (
        f"Stage {a.stage}: squash-merge {what} into `{_wb(a, cfg)}` per {PRACTICES_DIR}/branches.md. Where each branch/worktree is: {'; '.join(parts)}. A task that lists several repos has one job branch in each: merge the branch in EVERY listed repo into that repo's `{_wb(a, cfg)}` (the nested repo too, one squash commit per repo), then run the tests; only after they are green run each merged task's `wt.py remove` command from its part. On FAIL leave the worktrees untouched. Commit subject: reuse each job branch's own subject (`git log -1 --format=%s <branch>`; the commit hook wants `<type>(<area>): <what>`). Test command: {gate_text(cfg, merge=True)} via qrun. `wt.py remove` refuses while a branch is unmerged: never use --force. "
        f"If there is no branch to merge (work done in place on `{_wb(a, cfg)}`), nothing to merge = DONE. "
        f"After merging, run the project tests on top of `{_wb(a, cfg)}` via qrun. VERDICT: DONE = merged and green; FAIL = conflict or red tests; BLOCKED = anything else (missing brief or tool, environment error): change nothing and say why."
        + tail(a, "{rid}", allowed(DONE, FAIL, BLOCKED), "what was merged and the test result")
    )


def _needs_merge_pause(flags, pub):
    """True when a post-merge step (push, republish, index, live check) follows the master merge."""
    return bool(flags["push"] or pub or flags["index"] or flags["release"] or (flags["ui"] and pub and flags["live"]))


def _has_nested(a):
    import wt  # lazy: wt imports this package

    return bool(wt.nested_repos(repo_path(a.dir)))


def _nested_note(a, br, rel):
    """-> '; the `local-merge` step already squashed the nested repos <paths>', else ''."""
    import wt  # lazy: wt imports this package

    repo = repo_path(a.dir)
    have = [n for n in wt.nested_repos(repo) if wt.git(os.path.join(repo, n), "rev-parse", "--verify", "--quiet", f"refs/heads/{br}", check=False)]
    if not have:
        return ""
    return f"; the nested repos {', '.join(have)} are local-only and hold `{br}` too: the `local-merge` step already squashed them into `{rel}`"


def _release_message(a, cfg, name):
    """Lead message of `master-merged`: how to squash the stage branch into the release branch (never --ff-only)."""
    S, br, rel = a.stage, _wb(a, cfg), cfg["branch_release"]
    skip = f"then run `{tool_cmd('orch.py')} skip {name_of_stage(S)} --step master-merged` so cleanup, push, republish and index follow."
    extra = _nested_note(a, br, rel) if pr_mode.derived(a) else ""
    if pr_mode.derived(a) and pr_mode.pr_active(a):
        return f"Stage {S}: CI is green on the PR of `{br}`. Squash-merge it on GitHub with `gh pr merge {br} --squash --delete-branch` (title `<type>(<area>): <what>`){extra}, {skip}"
    if pr_mode.gh_down(a):
        return f"Stage {S}: {pr_mode.down_reason(a)}, and the repo has an `origin`, so nothing merged `{br}` (no PR steps, no `local-merge`, no push). Merge it into `{rel}` by hand (`gh pr merge {br} --squash` after `gh auth login`, or a local squash and push), confirm with `git log {rel} -1`, {skip}"
    if pr_mode.derived(a):
        return f"Stage {S}: the `local-merge` step already squashed `{br}` into `{rel}` in each repo that has no PR flow (no `origin`, no usable `gh`, pr set to false, and the nested repos). Confirm with `git log {rel} -1`, {skip}"
    return f"Stage {S}: merge `{br}` into `{rel}` now with `git merge --ff-only` (practices/branches.md), {skip}"


class _Final:
    """What the post-audit step builders share: the stage, its TASKS.md facts and the pr_mode switches."""

    def __init__(self, a, flags, cfg):
        self.a, self.flags, self.cfg = a, flags, cfg
        self.S = a.stage
        tm = os.path.join(paths(a)[0], TASKS_FILE)
        txt = Path(tm).read_text(encoding="utf-8") if os.path.exists(tm) else ""
        self.pub = re.search(PUBLISH_RE, txt, re.I | re.M)
        self.pub_line = " ".join(re.findall(PUBLISH_LINE_RE, txt, re.I | re.M))  # every PUBLISH: line, not just the first
        self.pre = re.search(r"^\s*PRE-APPROVED\s*:\s*master merge\s*$", txt, re.I | re.M)  # owner pre-approved the merge: no pause
        self.name = getattr(a, "feature", None) or self.S
        self.last_phase = "/phases/" not in self.S or is_finish_phase(tm)  # a feature's PR is ready once, after its closing phase
        self.rel_on = (
            self.last_phase and flags["release"] and bool(cfg.get("release_script")) and pr_mode.cleanup_active(a)
        )  # a git repo with a derived stage branch and a project release hook

    def script(self, sid, verb, why) -> dict:
        """A script step that runs `pr_mode.cmd(..., verb)`."""
        return step(sid, "script", None, pr_mode.cmd(self.a, self.name, verb), {DONE}, None, why)


def _bump_and_ready(c):
    if c.rel_on:
        yield [
            c.script(
                "version-bump",
                "version-bump",
                "last phase, after the audits: bump the version (patch unless TASKS.md says `release: minor|major`) and commit it on the stage branch",
            )
        ]
    if c.last_phase and pr_mode.pr_active(c.a):
        yield [c.script("pr-ready", "ready", "after the last audit: mark the PR ready and wait for green CI (red = BLOCKED)")]


def _acceptance(c):
    """After the final test, before the merge: the Plan.md `- cmd:` lines run from the merged stage branch in a fresh worktree (script, no model). FAIL stops the stage for the lead."""
    plan = Path(c.a.dir) / WORK_DIR / str(c.S).split("/phases/")[0] / PLAN_FILE
    items = acceptance_items(plan.read_text(encoding="utf-8")) if plan.exists() else []
    if c.last_phase and c.flags["test"] and any(tag for tag, _ in items):
        yield [
            step(
                ACCEPTANCE_STEP,
                "script",
                None,
                f"{tool_cmd('acceptance_step.py')} --dir {shlex.quote(os.path.abspath(c.a.dir))} --stage {c.S} --name {c.name}",
                {DONE},
                None,
                "before the merge: run the Plan.md acceptance commands from the stage branch in a fresh worktree (removed after); eyes: items are listed as MANUAL",
            )
        ]


def _local_merge(c):
    a = c.a
    if c.last_phase and pr_mode.local_merge_active(a) and (pr_mode.direct_flow(a) or _has_nested(a)) and _needs_merge_pause(c.flags, c.pub):
        yield [
            c.script(
                "local-merge",
                "local-merge",
                "before the release: squash the stage branch into the release branch in each local-only repo (conflict or dirty tree = BLOCKED)",
            )
        ]


def _master_merged(c):
    a = c.a
    if c.last_phase and pr_mode.pr_active(a):
        yield [
            c.script(
                "master-merged",
                "merge",
                "squash-merge the green PR into the release branch (draft, red, pending or conflicting = BLOCKED: the lead merges by hand and skips this step)",
            )
        ]
    elif not c.pre or pr_mode.gh_down(a):  # a lead must merge by hand when gh is down, pre-approved or not
        yield [
            {
                **step("master-merged", "lead", None, _release_message(a, c.cfg, c.name), {DONE}),
                "pause": True,
            }
        ]


def _merge_release_cleanup(c):
    if not _needs_merge_pause(c.flags, c.pub):  # PRE-APPROVED only drops the lead's confirmation pause: the merge, release and cleanup steps stay
        return
    yield from _master_merged(c)
    if c.rel_on:
        yield [
            c.script(
                "release",
                "release",
                "after the merge: tag the landed commit vX.Y.Z, push the tag and create the GitHub release (local-only repo: tag only)",
            )
        ]
    if c.last_phase and pr_mode.cleanup_active(c.a):
        yield [c.script("branch-cleanup", "cleanup", "after the release: delete the stage branch (local and remote), prune, back on the release branch")]


def _push_master(c):
    a, flags, S = c.a, c.flags, c.S
    if (
        flags["push"] and flags["merge"] and (pr_mode.direct_flow(a) or not pr_mode.derived(a)) and pr_mode.has_origin(a)
    ):  # with the PR flow the squash lands on origin through GitHub (a protected branch is never pushed directly); a repo without `origin` has nothing to push
        rel = c.cfg["branch_release"]
        yield [
            ops_as_script(
                a,
                c.cfg,
                step(
                    "push-master",
                    "ops",
                    MODEL_HAIKU,
                    f"Stage {S}: after the release into `{rel}`, push the release branch in each repo that was merged: `git -C <repo> -c credential.helper='!gh auth git-credential' push origin {rel}` (a nested repo: its own release branch, the one the stage branch was squashed into). "
                    f"Write .work/{S}/push-master.md: first line `VERDICT: DONE|FAIL`, then one line per repo (pushed / local-only / error). A repo with no `origin` remote (`git remote` empty) is local-only: report it as such, never FAIL."
                    + tail(a, "{rid}", allowed(DONE, FAIL), "the failing repo and error"),
                    {DONE},
                    None,
                    "after the release merge: push the release branch",
                ),
                "push",
            )
        ]


_REPUBLISH_HEAD = "Stage {S}: read the `PUBLISH:` line in .work/{S}/TASKS.md and republish each listed artifact (read the live artifact first). "
_REPUBLISH_RECIPE = (
    "URL {url} has a build recipe: run `{build}` in `{cwd}` (its scratch dir is `{out_dir}`). "
    "Then read the live artifact to learn its layout. "
    "Then publish `{page}`{files} to that URL in place: never create a new artifact, omit the icon, keep the capabilities. "
)
_REPUBLISH_FILES = " plus these files ({pairs}; paths relative to `{cwd}`)"
_REPUBLISH_NO_RECIPE = "A URL with no `publish` entry in pipeline.json: no build recipe configured, report FAIL if the page cannot be built. "


def _republish_brief(a, S, pub_line, entries):
    """-> the republish brief: the base text, then per URL of the PUBLISH line with a `publish` entry its build recipe, then the no-recipe rule."""
    out_dir = os.path.join(os.path.abspath(a.dir), WORK_DIR, S, SCRATCH_DIR)
    text = _REPUBLISH_HEAD.replace("{S}", str(S))
    listed = set(re.split(r"[\s,;]+", pub_line))  # whole URLs: one URL that prefixes another must not match both
    for url, e in entries.items():
        if url not in listed:
            continue
        cwd = os.path.normpath(os.path.join(os.path.abspath(a.dir), e[PUBLISH_CWD]))
        pairs = ", ".join(f"`{pub}` from `{src.replace(PUBLISH_OUT, out_dir)}`" for pub, src in e[PUBLISH_FILES].items())
        text += _REPUBLISH_RECIPE.format(
            url=url,
            build=e[PUBLISH_BUILD].replace(PUBLISH_OUT, shlex.quote(out_dir)),
            cwd=cwd,
            out_dir=out_dir,
            page=e[PUBLISH_PAGE].replace(PUBLISH_OUT, out_dir),
            files=_REPUBLISH_FILES.format(pairs=pairs, cwd=cwd) if pairs else "",
        )
    return text + _REPUBLISH_NO_RECIPE


def _publish_steps(c):
    a, flags, S = c.a, c.flags, c.S
    if c.pub:
        yield [
            step(
                "republish",
                "publisher",
                MODEL_HAIKU,
                _republish_brief(a, S, c.pub_line, c.cfg[PUBLISH_KEY])
                + f"Write .work/{S}/republish.md: first line `VERDICT: DONE|FAIL`."
                + tail(a, "{rid}", allowed(DONE, FAIL), "the artifact that failed"),
                {DONE},
                None,
                "after push: republish artifacts",
            )
        ]
    if flags["index"]:
        yield [
            step(
                "index-refresh",
                "script",
                None,
                f"{tool_cmd('index_step.py')} --dir {shlex.quote(os.path.abspath(a.dir))}",
                {DONE, FAIL},
                None,
                "after merge/publish: refresh the code index (a failure is only a warning in the retro)",
            )
        ]
    if flags["ui"] and c.pub and flags["live"]:
        yield [
            step(
                "live-check",
                "web-tester",
                MODEL_HAIKU,
                f"Stage {S}: open each URL of the `PUBLISH:` line in .work/{S}/TASKS.md and run the smoke list in .work/{S}/live-check.md (what to open, what must show). "
                f"VERDICT: PASS = every item shows as listed; FAIL = any item differs (name it)."
                + UNVERIFIED_RULE
                + tail(a, "{rid}", allowed(DONE, FAIL, BLOCKED), "the failed items"),
                {DONE, PASS},
                None,
                "after publish: live pages match the smoke list",
            )
        ]
        yield [_serve_stop_step(a, "live-check-stop")]


def _cleanup_step(c):
    a, S = c.a, c.S
    if c.flags["clean"]:
        yield [
            ops_as_script(
                a,
                c.cfg,
                step(
                    "cleanup",
                    "ops",
                    MODEL_HAIKU,
                    f"Stage {S}: runs last, after publish. First run `{tool_cmd('wt.py')} clean --stage {S} --dir {os.path.abspath(a.dir)}` (removes leftover job worktrees and branches; skip when pipeline.json has no `worktrees`). In {os.path.abspath(a.dir)}/.work/{S}/ delete the finished plan and task files: task-*.md (including task-N-list.md and any other task-*-*.md), reports/report-a*.md except your own reports/report-{{rid}}.md and every report whose first line is a BLOCKED, FAIL or ESCALATE verdict (the retro reads them), leads/ (all files), and scratch lists the tasks created. KEEP TASKS.md (the stage index), orch.jsonl and every other file. Delete nothing outside .work/{S}/ and never touch another stage's folder. "
                    f"Write .work/{S}/cleanup.md: first line `VERDICT: DONE|FAIL`, then `deleted N files` and the kept file names."
                    + tail(a, "{rid}", allowed(DONE, FAIL), "the file that could not be deleted"),
                    {DONE, FAIL},
                    None,
                    "after publish: remove finished task and plan files (a failure is only a warning)",
                ),
                "cleanup",
            )
        ]


_FINAL_BUILDERS = (_bump_and_ready, _acceptance, _local_merge, _merge_release_cleanup, _push_master, _publish_steps, _cleanup_step)


def final_phases(a, flags, tasks, evs) -> Iterator[list[dict]]:
    """Audit phases, then the post-publish steps (flag `index`; `UI: yes` + `PUBLISH:` line)."""
    cfg = project(a.dir)
    yield from audit_phases(a, flags, tasks, evs)
    c = _Final(a, flags, cfg)
    for build in _FINAL_BUILDERS:
        yield from build(c)


def _final_test_step(a, cfg, ids, sid, ok_s, ok_t):
    S = a.stage
    if cfg.get("gates") or cfg.get(MERGE_GATES_KEY) or cfg.get(FINAL_GATES_KEY):
        return step(
            sid,
            "script",
            None,
            f"{tool_cmd('audit/final_check.py')} --dir {shlex.quote(a.dir)} --stage {S}",
            ok_s,
            None,
            "final verification by script: gates and per-path checks from pipeline.json, no model",
        )
    return step(
        sid,
        "tester",
        MODEL_HAIKU,
        f"Stage {S}: verify the merged result on branch `{_wb(a, cfg)}` (tasks {ids}): the full test suite, linters and build via qrun, and the done criteria in .work/{S}/TASKS.md. For node tests: {test_env_hint(cfg)}pass globs not directories; run python tests as python3 -I <file>. Warning gate (owner 2026-10-28): also run every command listed under `each` in pipeline.json, once per match (qrun); ANY warning is FAIL: name the code and count, the fix goes to the check (if it is wrong) or the content, never an ignore."
        + tail(a, "{rid}", allowed(PASS, FAIL)),
        ok_t,
    )


def _serve_stop_step(a, sid):
    """Script safety net after a web-tester step: stop the dev servers it started (the TTL ends them anyway)."""
    return step(
        sid,
        "script",
        None,
        f"{tool_cmd('servectl.py')} stop --state-dir {shlex.quote(serve_dir(a))}",
        {DONE, FAIL},
        None,
        "stop any dev server the web-tester left running (servectl; the TTL is the second net)",
    )


def _web_test_step(a, cfg, ids, sid, ok, prev=""):
    """One browser check of the UI tasks IDS (a batch): every `Done:` criterion of each task, the project flows, both widths and themes. PREV: the report of the earlier run of this batch; a re-check then verifies only its FAIL and UNVERIFIED items."""
    S = a.stage
    return step(
        sid,
        "web-tester",
        webtest_model(cfg),
        f"Stage {S}: browser-check the UI changed by tasks {ids} on branch `{_wb(a, cfg)}`: every criterion of the `Done:` of each of these tasks (.work/{S}/task-N.md), no item cap (this step holds at most {WEBTEST_BATCH} tasks)."
        f" UI paths (globs): {', '.join(ui_paths(cfg))}."
        f" Serve only with servectl and this state dir: `--state-dir {shlex.quote(serve_dir(a))}`."
        + webtest_build_note(cfg)
        + ux_flows_note(a, cfg)
        + head_note(a)
        + f" Check {WEBTEST_WIDTHS} in light and dark, include one wrong-input path and the end state of a flow (a session's finish screen) when a task changes it; read console and network messages."
        + (
            f" Re-check: the fix tasks ran; verify only the FAIL and UNVERIFIED items of the earlier report {prev} and say PASS or FAIL per item."
            if prev
            else ""
        )
        + UNVERIFIED_RULE
        + tail(a, "{rid}", allowed(PASS, FAIL, BLOCKED)),
        ok,
        WEBTEST_EFFORT,
    )


def _ui_batches(a, cfg, tasks, fallback=True):
    """-> the task ids of TASKS in batches of WEBTEST_BATCH: the tasks whose Predicted paths match `ui_paths` (all of them when none does and FALLBACK, as a path may sit in a nested repo)."""
    d = os.path.join(os.path.abspath(a.dir), WORK_DIR, a.stage)
    globs = ui_paths(cfg)
    hit = [t["id"] for t in tasks if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.basename(p), g) for p in task_paths(d, t["id"]) for g in globs)]
    ids = hit or ([t["id"] for t in tasks] if fallback else [])
    return [ids[i : i + WEBTEST_BATCH] for i in range(0, len(ids), WEBTEST_BATCH)]


def _web_test_id(k, again=False, fix=False):
    """-> the step id of batch K (0-based): the first batch keeps WEBTEST_STEP; FIX batches (the tasks the fix round added) are numbered apart."""
    suffix = f"{WEBTEST_FIX_SUFFIX}{k + 1}" if fix else (f"{WEBTEST_BATCH_SUFFIX}{k + 1}" if k else "")
    return WEBTEST_STEP + suffix + (WEBTEST_AGAIN_SUFFIX if again else "")


def _split_fix_tasks(evs, tasks):
    """-> (the tasks that existed when the fix round started, the tasks it added). Before that round, or when no task has a start event, every task is original: the batch layout never shifts under a finished check."""
    fix = [e for e in history(evs, FIX_WEB_STEP) if e["ev"] == EV_DISPATCH]
    if not fix:
        return tasks, []
    before = {e["task"] for e in evs if e.get("ev") in STARTED_EVENTS and e.get("task") and e.get("t", 0) < fix[0]["t"]}
    orig = [t for t in tasks if t["id"] in before]
    return (orig, [t for t in tasks if t["id"] not in before]) if orig else (tasks, [])


def _web_test_phases(a, cfg, tasks, evs):
    """Browser check in batches, then one fix round for the FAILs, one re-check of every batch that failed or stayed unverified, and a check of the tasks the fix round added."""
    if webtest_tools_missing(
        cfg
    ):  # no browser tools in headless sessions: record the skip visibly (UNVERIFIED-WEB), do not dispatch a session that can only BLOCK
        ids = ", ".join(i for b in _ui_batches(a, cfg, tasks) for i in b)
        yield [
            step(
                WEBTEST_STEP,
                "script",
                None,
                f"{tool_cmd('ops_steps.py')} webskip --dir {shlex.quote(os.path.abspath(a.dir))} --stage {a.stage} --ids {shlex.quote(ids)}",
                {DONE},
                None,
                "no browser tools in headless sessions (pipeline.json webtest_tools_available false): the browser check is recorded as skipped (UNVERIFIED-WEB), not run",
            )
        ]
        return
    orig, added = _split_fix_tasks(evs, tasks)
    batches = _ui_batches(a, cfg, orig)
    for k, ids in enumerate(batches):
        yield [_web_test_step(a, cfg, ", ".join(ids), _web_test_id(k), {PASS, FAIL, BLOCKED})]
    bad = [k for k in range(len(batches)) if last_verdict(evs, _web_test_id(k)) in (FAIL, BLOCKED)]
    failed = [last_report(evs, _web_test_id(k)) for k in bad if last_verdict(evs, _web_test_id(k)) == FAIL]
    if failed:
        yield [fix_step(a, tasks, FIX_WEB_STEP, failed, "the browser check failed.", FIX_WEB_STEP, f"{WEBTEST_STEP} FAILED")]
    for k in bad:
        yield [_web_test_step(a, cfg, ", ".join(batches[k]), _web_test_id(k, True), {PASS}, prev=last_report(evs, _web_test_id(k)))]
    for k, ids in enumerate(_ui_batches(a, cfg, added, fallback=False) if added else []):
        yield [_web_test_step(a, cfg, ", ".join(ids), _web_test_id(k, fix=True), {PASS})]
    yield [_serve_stop_step(a, f"{WEBTEST_STEP}-stop")]


def _stage_head(a, cfg):
    """-> the commit the stage's work branch points at, "" when git cannot say."""
    try:
        return gitx.out(a.dir, "rev-parse", _wb(a, cfg))
    except (RuntimeError, OSError):
        return ""


def _with_head(a, cfg, st):
    """STEP with the stage head it is about to run on (logged on its dispatch event)."""
    return {**st, "head": _stage_head(a, cfg)}


def _head_moved(a, cfg, evs, sid):
    """True when the dispatch of step SID recorded a stage head and the branch is on another commit now."""
    rec = next((e.get("head") for e in history(evs, sid) if e["ev"] == EV_DISPATCH and e.get("head")), "")
    return bool(rec) and bool(_stage_head(a, cfg)) and rec != _stage_head(a, cfg)


def _preflight_head(a):
    """-> the sha of the preflight marker of this stage's feature folder ('' when none): the base a red test is compared with.
    Read here, not via branches/graph: importing them makes an import cycle (graph -> phases -> branches -> graph)."""
    name = str(getattr(a, "feature", None) or getattr(a, "stage", ""))  # the name graph._name gives
    try:
        with open(os.path.join(a.dir, WORK_DIR, name.split("/phases/")[0], PREFLIGHT_MARKER), encoding="utf-8") as f:
            return str(json.load(f).get("head", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def _triage_step(a, report, base_head):
    """Script after a failed final-test: rerun the failing test files alone; then on the preflight head (steps_finaltest.py)."""
    cmd = f"{tool_cmd('pipeline/steps_finaltest.py')} --dir {shlex.quote(a.dir)} --stage {a.stage} --report {shlex.quote(report)}"
    cmd += f" --base-head {shlex.quote(base_head)}" if base_head else ""
    return step(
        FINALTEST_TRIAGE_STEP,
        "script",
        None,
        cmd,
        {DONE, FAIL, BLOCKED},
        None,
        "final-test FAILED: rerun its failing test files alone, then on the preflight head (by script)",
    )


def _doc_cites_step(a):
    """Script at every stage end: the cites of every doc under the docs folder must hold (steps_finaltest.py --doc-cites); FAIL stops the driver, no fix flow."""
    cmd = f"{tool_cmd('pipeline/steps_finaltest.py')} --dir {shlex.quote(a.dir)} {DOC_CITES_FLAG}"
    return step(
        DOC_CITES_STEP,
        "script",
        None,
        cmd,
        {DONE},
        None,
        "docs: every path:line cite in the whole docs folder must hold (by script, changed docs or not)",
    )


def _test_phases(a, cfg, flags, tasks, evs):
    """Final verification, each with one bounded fix round when it comes back FAIL. A failed run is triaged first: flaky -> no fix; PRE_EXISTING_RED -> the lead."""
    ids = ", ".join(t["id"] for t in tasks)
    yield [
        _with_head(a, cfg, _final_test_step(a, cfg, ids, "final-test", {DONE, FAIL}, {PASS, FAIL}))
    ]  # a red result is not a stop: one bounded fix round first, then the lead
    latest = "final-test"
    if last_verdict(evs, latest) == "FAIL" and _head_moved(a, cfg, evs, latest):  # the report is old: judge the code as it is now before planning fixes
        latest = FINAL_TEST_RERUN_STEP
        yield [_with_head(a, cfg, _final_test_step(a, cfg, ids, latest, {DONE, FAIL}, {PASS, FAIL}))]
    if last_verdict(evs, latest) == "FAIL":
        yield [_triage_step(a, last_report(evs, latest), _preflight_head(a))]
        triage = last_verdict(evs, FINALTEST_TRIAGE_STEP)
        if triage is None:  # the triage has not run yet: the driver runs it, then asks again with its verdict
            return
        if triage == BLOCKED:  # red on the preflight head too: not the stage's doing; the triage report names it
            return
        if triage != DONE:  # FAIL (red on the stage head only): the fix plan; DONE is flaky, no fix
            yield [
                fix_step(
                    a,
                    tasks,
                    "fix-plan-test",
                    [last_report(evs, latest)],
                    "the final verification (full test suite and checks) failed.",
                    "fix-plan-test",
                    "final-test FAILED",
                )
            ]
            yield [_final_test_step(a, cfg, ids, "final-test-2", {DONE}, {PASS})]  # after the fix tasks have run
    if flags["ui"]:  # after final-test: the browser check needs the rebuilt WASM, and a failed suite makes it pointless
        yield from _web_test_phases(a, cfg, tasks, evs)


def _docs_step(a, cfg):
    S = a.stage
    return step(
        "final-docs",
        "docs-writer",
        MODEL_HAIKU,
        f"Stage {S}: update mode. Docs root, first match wins: a `docs:` line in .work/{S}/TASKS.md, then in PROGRESS.md, then the `## Docs` section of the project CLAUDE.md in the workspace root (it names the repo and its docs/ folder; find the repo there, do not search the tree); `docs: skip` -> nothing to do, VERDICT: DONE; no `docs:` line and the CLAUDE.md names no docs folder -> VERDICT: DONE with a one-line note, write nothing. Changed files: git diff of the stage on `{_wb(a, cfg)}`; collect the DOCS-STALE lines of .work/{S}/report-*.md. When the docs are done, commit them on `{_wb(a, cfg)}`: `git add` only the files you changed (docs/ and the CLAUDE.md Docs section), subject `docs(<scope>): <what>`, TZ=UTC, `-F msgfile`; leave nothing uncommitted."
        + _index_modules_note(a, cfg)
        + " Before VERDICT: DONE run `cs-docs-index --root <repo root> --check` and fix what it reports, including `describes no existing module`."
        + f" Before VERDICT: DONE run `{tool_cmd('ops_steps.py')} public-words --dir {shlex.quote(os.path.abspath(a.dir))}` and fix every file:line it prints (a clean run prints nothing to fix)."
        + tail(a, "{rid}", allowed(DONE, BLOCKED)),
        {DONE},
    )


def _index_modules_note(a, cfg):
    """Brief text naming the index modules; '' when the root is no git repo or has no docs/ folder."""
    root = repo_dir(a.dir, cfg)
    if not os.path.exists(os.path.join(root, ".git")) or not os.path.isdir(os.path.join(root, "docs")):
        return ""
    listed = ", ".join(f"{m} (`{slug(m)}`)" for m in module_headings(root)) or "none"
    return (
        f" Index modules: {listed}. Write docs/modules/<slug>.md only for a listed module; "
        "fold the content of a folder that is not an index module into its parent module doc."
    )


def audit_phases(a, flags, tasks, evs) -> Iterator[list[dict]]:
    cfg = project(a.dir)
    """Lazy: a later phase is built only after the earlier one is satisfied, so it can read the earlier verdicts from evs."""
    if flags["test"]:
        yield from _test_phases(a, cfg, flags, tasks, evs)
    audits_follow = flags["audit"] and audit_due(paths(a)[0], len(tasks), int(cfg.get("audit_every_tasks") or DEFAULT_EVERY), a.dir)
    beside = []
    if flags["docs"]:
        if audits_follow and docs_beside_audits(a, cfg, audit_dir_of(a, cfg)):
            beside = [_docs_step(a, cfg)]  # no file the docs writer edits is in the stage diff: starts with the audits
        else:
            yield [_docs_step(a, cfg)]
    cite_on = cfg.get(DOC_CITES_KEY, DOC_CITES_DEFAULT)  # every stage end, docs flag or not: the whole docs folder, not only the stage's changes
    if cite_on and not beside:
        yield [_doc_cites_step(a)]
    if flags["test"]:  # recall is logged for every tested stage and phase, not only audited ones
        yield [
            step(
                "plan-vs-changes",
                "script",
                None,
                f"{tool_cmd('localmodel/plan_context.py')} stage {audit_dir_of(a, cfg)} --stage-dir .work/{a.stage} --base {audit_base(a, cfg)} --head {_wb(a, cfg)} --out .work/{a.stage}/audits/plan-vs-changes.md --workspace {shlex.quote(os.path.abspath(a.dir))} --verdict",
                {DONE},
                None,
                "after the tests: planned tasks vs changed files and the context recall, by script (best effort; logs context-recall for every stage)",
            )
        ]
    if not audits_follow:
        return
    rounds = audit_rounds(a, cfg, flags, tasks, evs, beside)
    if beside and cite_on:  # the beside docs writer runs in the first audit batch: the cite step follows that batch
        rounds = _after_first_batch(rounds, [_doc_cites_step(a)])
    yield from rounds


def _after_first_batch(batches, extra):
    """Yield the first batch, then `extra` as its own batch, then the rest."""
    it = iter(batches)
    first = next(it, None)
    if first is not None:
        yield first
    yield extra
    yield from it
