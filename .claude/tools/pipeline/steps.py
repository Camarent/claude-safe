"""Per-task state machine (dev -> reviewer rounds), step dicts and the parallel-phase runner."""

import hashlib
import os
import re

from .briefs import ENV_DRY_NO_CCN, prompt_for, qualityrun_on, testrun_on, webtest_prompt
from .ccn import review_ccn_hit
from .classify import STOP, classify_test_run
from .configuration import (
    BLOCKED,
    DIFF_SHA_LEN,
    DONE,
    ENV_PREREVIEW_SKIP,
    ESCALATE,
    EV_DISPATCH,
    EV_REPORT,
    EV_RETRY,
    EV_SKIP,
    FAIL,
    GATE_FREE_ROUNDS,
    GATE_PLAN_ERROR,
    HAIKU_DEV_TRIES,
    MAX_ROUNDS,
    MAX_TRIES,
    MODEL_HAIKU,
    MODEL_SONNET,
    PASS,
    PHASES_DIR,
    PLAN_NOTES_FILE,
    PREREVIEW_DIFF_CHARS,
    PREREVIEW_FREE_ROUNDS,
    PREREVIEW_SKIP_ASK,
    PREREVIEW_SKIP_REVIEWER,
    RULES_FILE,
    SKIPPED,
    WEB_TESTER,
    WORK_DIR,
    driver_gates_on,
    gates_of,
    project,
    repo_dir,
    tool_cmd,
)
from .plan_files import paths_in, task_type
from .stagedir import resolve_stage
from .util import read_text, work_path, write_text
from .worktrees import deliverable_texts, files_sha, job_diff, job_dirty, job_heads, uncommitted_diff


def history(evs, tid):
    """Events of one task/step since its last `retry` (the owner's reset)."""
    mine = [e for e in evs if e.get("task") == tid]
    for i in range(len(mine) - 1, -1, -1):
        if mine[i]["ev"] == EV_RETRY:
            return _after_retry(mine, i)
    return mine


def _after_retry(mine, i):
    """The events of a task that survive its retry marker `mine[i]` (the owner's reset, or a driver re-run)."""
    marker = mine[i]
    if marker.get("from") == "script":  # a lost script step: only its own dispatch goes, the task's events stay
        return [e for e in mine[:i] if e.get("id") != marker.get("id")] + mine[i + 1 :]
    if marker.get("from") == "review":  # keep the dev events, drop the reviewer's
        return [e for e in mine[:i] if e["ev"] != EV_RETRY and e.get("role") != "reviewer"] + mine[i + 1 :]
    if marker.get("auto"):  # an unclear dev report: the review rounds it was asked to fix survive
        rounds = [e for e in mine[:i] if e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] in (FAIL, ESCALATE)]
        return rounds + mine[i + 1 :]
    return mine[i + 1 :]


def hint(a, tid):
    return f"; fix the cause, then `orch.py retry --stage {a.stage} --step {tid}` (or `skip`)"


def script_ids(mine, word):
    """Ids of this task's script dispatches whose `why` starts with `word` (pre-review, test run)."""
    return {e["id"] for e in mine if e["ev"] == EV_DISPATCH and e.get("role") == "script" and (e.get("why") or "").startswith(word)}


def failing_reports(mine):
    """Every dev ESCALATE, reviewer FAIL/ESCALATE and red test-run FAIL report of the task, oldest first."""
    test_ids = script_ids(mine, "test run")
    return [
        e
        for e in mine
        if e["ev"] == EV_REPORT
        and (
            (e["role"] == "dev" and e["verdict"] == ESCALATE)
            or (e["role"] == "reviewer" and e["verdict"] in (FAIL, ESCALATE))
            or (e["id"] in test_ids and e["verdict"] == FAIL)
        )
    ]


def failed_event(mine):
    """The latest of them: what the escalation gate judges."""
    return next(iter(reversed(failing_reports(mine))), None)


def _reviewer_reports(mine, verdict):
    return sum(1 for e in mine if e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] == verdict)


def _failed_rounds(mine):
    """Failed review rounds: every reviewer FAIL, plus an ESCALATE from a Sonnet reviewer (nobody stronger is left, so it counts as a FAIL)."""
    sonnet_ids = {e["id"] for e in mine if e["ev"] == EV_DISPATCH and e["role"] == "reviewer" and e["model"] == MODEL_SONNET}
    return sum(
        1 for e in mine if e["ev"] == EV_REPORT and e["role"] == "reviewer" and (e["verdict"] == FAIL or (e["verdict"] == ESCALATE and e["id"] in sonnet_ids))
    )


def _reports(mine, role, verdict, ids=None):
    """How many report events of ROLE (any role when None) with VERDICT; only those whose id is in IDS when given."""
    return sum(1 for e in mine if _is_report(e, role, verdict, ids))


def _is_report(e, role, verdict, ids):
    return e["ev"] == EV_REPORT and (role is None or e["role"] == role) and e["verdict"] == verdict and (ids is None or e["id"] in ids)


class _TaskState:
    """What next_step derives once from a task's events: review rounds, escalations, pre-review script ids, who is Sonnet."""

    def __init__(self, a, task, mine):
        self.rounds = _failed_rounds(mine)
        self.rev_esc = _reviewer_reports(mine, ESCALATE) > 0  # a Haiku reviewer ESCALATE sends the task to the Sonnet reviewer
        self.dev_esc = _reports(mine, "dev", ESCALATE)
        self.test_ids = script_ids(mine, "test run")
        self.qual_ids = script_ids(mine, "quality run")
        self.gate_ids = script_ids(mine, "task gates")
        self.test_fails = _reports(mine, None, FAIL, self.test_ids)
        self.dev_done = _reports(mine, "dev", DONE)
        self.sonnet_dev = sonnet_dev_dispatched(mine)
        self.reviewer_sonnet = (
            task["review_sonnet"] or self.rev_esc or sonnet_review_dispatched(mine)
        )  # the reviewer goes to Sonnet only by itself (gate or its own ESCALATE) and then stays; a Sonnet dev does not move it


REPORT_WHY = "report task: content review on Sonnet"


def is_report(a, task):
    """True when the task file says `Type: report`: its deliverable is a file, not a repo diff (no quality run; the local pre-review judges the file, Sonnet reviewer)."""
    repo = getattr(a, "dir", ".")
    return task_type(read_text(work_path(os.path.join(repo, WORK_DIR, a.stage), task["id"] + ".md"))) == "report"


def _disp(a, task, rid, role, sonnet, rework=None, free=False):
    return {
        "free": free,  # logged on the dispatch event: a rework after a local pre-review FAIL is not a dev try
        "action": "dispatch",
        "id": rid,
        "agent": role,
        "model": MODEL_SONNET if sonnet else MODEL_HAIKU,
        "task": task["id"],
        "role": role,
        "prompt": prompt_for(a, task, role, rework, rid),
    }


def _dirty_rework(a, task, mine, rid):
    """Uncommitted files in the job worktree after a dev DONE: back to the dev (a failed try under MAX_TRIES) with the file list as its rework file; None when clean."""
    dirty = job_dirty(a, task["id"])
    if not dirty:
        return None
    f = work_path(os.path.join(getattr(a, "dir", "."), WORK_DIR, a.stage), f"dirty-{task['id']}-{rid}.md", create=True)
    write_text(
        f,
        f"The job worktree has uncommitted changes, so it cannot be reviewed:\n{chr(10).join('- ' + x for x in dirty[:20])}\n"
        "Commit them with wt.py commit if they belong to the task, otherwise remove the change by editing the file back by hand. NEVER run `git checkout --`, `git restore` or `git reset`: they destroy work. The worktree must be clean before review.\n",
    )
    return _disp(a, task, rid, "dev", task["owner_sonnet"] or _tries(mine, "dev", MODEL_HAIKU) >= HAIKU_DEV_TRIES, rework=f)


def _disp_reviewer(a, task, rid, s, mine=()):
    """The reviewer dispatch; Sonnet also when a changed function is above `review_ccn` (driver decision, any error -> Haiku). A dirty job worktree goes back to the dev."""
    back = _dirty_rework(a, task, mine, rid)
    if back:
        return back
    d = _disp_reviewer_model(a, task, rid, s)
    d["head"] = job_heads(a, task["id"])  # logged on the dispatch event: a later rework with the same HEAD added no commit
    return d


def _disp_reviewer_model(a, task, rid, s):
    if is_report(a, task):
        d = _disp(a, task, rid, "reviewer", True)
        d["why"] = "reviewer on Sonnet: " + REPORT_WHY
        return d
    if s.reviewer_sonnet:
        return _disp(a, task, rid, "reviewer", True)
    if os.environ.get(ENV_DRY_NO_CCN):  # dry run: no job worktree, the gate cannot fire
        return _disp(a, task, rid, "reviewer", False)
    why = review_ccn_hit(a, task["id"])
    d = _disp(a, task, rid, "reviewer", bool(why))
    if why:
        d["why"] = why
    return d


def _no_commit_ask(a, task, mine):
    """An ask_main when the job HEAD is the one the last reviewer FAIL saw (the rework added no commit), else None: no second paid review of the same code."""
    fail = next((e for e in reversed(mine) if e["ev"] == EV_REPORT and e.get("role") == "reviewer"), None)
    if not fail or fail["verdict"] != FAIL:
        return None
    disp = next((e for e in mine if e["ev"] == EV_DISPATCH and e["id"] == fail["id"]), None)
    old = (disp or {}).get("head") or ""
    if not old or old != job_heads(a, task["id"]):
        return None
    return {
        "action": "ask_main",
        "message": f"{task['id']}: rework added no commit since reviewer FAIL {fail['id']}; the reviewer is not run again on the same code. "
        f"Decide: fix by hand and commit, `orch.py retry`, or `orch.py skip`; finding {fail['file']}" + hint(a, task["id"]),
    }


def _script(task, rid, why, cmd):
    return {"action": "dispatch", "id": rid, "agent": "script", "model": None, "task": task["id"], "role": "script", "why": why, "prompt": cmd}


def _test_run_script(a, task, rid, why="test run (the task's Test lines in its worktree); FAIL goes back to the dev"):
    return _script(task, rid, why, f"{tool_cmd('testrun.py')} --stage {a.stage} --task {task['id']} --dir {getattr(a, 'dir', '.')}")


def _after_dev(a, task, mine, rid, s, last):
    if last["verdict"] == DONE:
        if testrun_on(a) and len(s.test_ids) < s.dev_done:  # the task's Test lines run by script after every dev DONE: the dev only edits
            return _test_run_script(a, task, rid)
        return _quality_or_review(a, task, mine, rid, s)
    return _after_dev_other(a, task, mine, rid, s, last)


GATES_LINE = re.compile(r"^\s*Gates:", re.MULTILINE)  # the dev report line: each configured gate RAN/SKIPPED + result


def _gates_rework(a, task, mine, rid):
    """A dev DONE report without a `Gates:` line while the project configures gates: back to the dev (a try, like any rework) with a note; None otherwise."""
    if os.environ.get(ENV_DRY_NO_CCN) or task_gates_on(a) or not gates_of(project(getattr(a, "dir", "."))):  # the dry run writes no real dev reports
        return None
    rep = next((e for e in reversed(mine) if e["ev"] == EV_REPORT and e.get("role") == "dev" and e["verdict"] == DONE), None)
    if rep is None or GATES_LINE.search(read_text(rep["file"])):
        return None
    f = work_path(os.path.join(getattr(a, "dir", "."), WORK_DIR, a.stage), f"gates-{task['id']}-{rid}.md", create=True)
    write_text(
        f,
        f"Your report {rep['file']} has no `Gates:` line. Run every gate listed in your brief (or say SKIPPED with the reason), then report one line: "
        "`Gates: <name> RAN <result>; <name> SKIPPED <why>; ...`.\n",
    )
    return _disp(a, task, rid, "dev", task["owner_sonnet"] or _tries(mine, "dev", MODEL_HAIKU) >= HAIKU_DEV_TRIES, rework=f)


def _quality_or_review(a, task, mine, rid, s):
    """Advisory quality run (lints/analysis of the touched files and repos), once per task, after the tests are green: findings go to the dev as one
    recommended extra round, never to the lead; later rounds and clean runs go on to the review. A missing `Gates:` line comes first."""
    if (
        task_gates_on(a) and len(s.gate_ids) < s.dev_done and not is_report(a, task)
    ):  # the driver runs the full gate set once per dev DONE (the script skips unchanged content)
        return _script(
            task,
            rid,
            "task gates: full gate set in the job worktree; FAIL goes back to the dev",
            f"{tool_cmd('audit/taskgates.py')} --stage {a.stage} --task {task['id']} --dir {getattr(a, 'dir', '.')}",
        )
    back = _gates_rework(a, task, mine, rid)
    if back:
        return back
    if qualityrun_on(a) and not s.qual_ids and not is_report(a, task):
        return _script(
            task,
            rid,
            "quality run (audit tools on the touched files, advisory); FAIL gives the dev one recommended extra round",
            f"{tool_cmd('audit/qualityrun.py')} --stage {a.stage} --task {task['id']} --dir {getattr(a, 'dir', '.')}",
        )
    return _to_review(a, task, mine, rid, s)


def _to_review(a, task, mine, rid, s):
    """The local pre-review (if on) before each paid review, then the paid reviewer. The pre-review is skipped once the dev used its last try, so
    the local model can never keep a task away from the paid reviewer (Haiku, Haiku, Sonnet, Sonnet: each earlier try faces the gate)."""
    if prereview_on(a) and _tries(mine, "dev") < MAX_TRIES and not _prereview_done(a, task, mine):
        return _script(
            task,
            rid,
            "local pre-review (lp review); FAIL goes back to the dev",
            f"{tool_cmd('localmodel/prereview.py')} --stage {a.stage} --task {task['id']} --dir {getattr(a, 'dir', '.')}",
        )
    return _no_commit_ask(a, task, mine) or _disp_reviewer(a, task, rid, s, mine)


DIFF_SHA_RE = re.compile(r"^diff-sha: (\w+)", re.M)


def _prereview_done(a, task, mine):
    """True when another pre-review is pointless: it already sent the task back PREREVIEW_FREE_ROUNDS times (counted over the whole event log, an auto-retry does
    not reset it), or the dev's rework left the diff as it was when the last pre-review FAILed (no new commit)."""
    full = [e for e in (getattr(a, "orch_events", None) or mine) if e.get("task") == task["id"]]
    ids = script_ids(full, "local pre-review")
    fails = [e for e in full if e["ev"] == EV_REPORT and e["id"] in ids and e["verdict"] == FAIL]
    if len(fails) >= PREREVIEW_FREE_ROUNDS:
        return True
    m = DIFF_SHA_RE.search(read_text(fails[-1]["file"])) if fails else None
    diff = ""
    if m:  # the same diff pre-review hashed: the job worktree's, else the uncommitted one in the main checkout
        diff = job_diff(a, task["id"], PREREVIEW_DIFF_CHARS)
        if not diff:
            repo = getattr(a, "dir", ".")
            diff = uncommitted_diff(repo, paths_in(read_text(work_path(os.path.join(repo, WORK_DIR, a.stage), task["id"] + ".md"))))
    if not m:
        return False
    if diff and hashlib.sha256(diff.encode("utf-8")).hexdigest()[:DIFF_SHA_LEN] == m.group(1):
        return True
    return _files_unchanged(a, task, m.group(1))


def _files_unchanged(a, task, sha):
    """True when the task's deliverable files hash to `sha` (file-mode pre-review FAILed on exactly these)."""
    repo = getattr(a, "dir", ".")
    texts = deliverable_texts(paths_in(read_text(work_path(os.path.join(repo, WORK_DIR, a.stage), task["id"] + ".md"))), repo)
    return bool(texts) and files_sha(texts) == sha


PLAN_GAP_RE = re.compile(
    r"^\s*(?:VERDICT:\s*)?ESCALATE: PLAN GAP ?[:\-\u2013\u2014]", re.MULTILINE
)  # a dev report line: the plan lacks something the task depends on (any separator: a Haiku dev wrote "PLAN GAP -")


def _plan_gap(last):
    return last["verdict"] == ESCALATE and bool(PLAN_GAP_RE.search(read_text(last["file"])))


NO_FIX_RE = re.compile(
    r"^\s*(?:VERDICT:\s*)?(?:BLOCKED|ESCALATE): NO FIX ?[:\-\u2013\u2014]\s*(.*)$", re.MULTILINE
)  # a dev report line: no model can fix this by editing (the cause is outside the repo, e.g. an upstream package with no fixed version)


def _no_fix(last):
    """-> the dev's reason when its BLOCKED or ESCALATE report carries the `NO FIX:` marker, else None."""
    m = NO_FIX_RE.search(read_text(last["file"])) if last["verdict"] in (BLOCKED, ESCALATE) else None
    return (m.group(1)[:300] or "no reason given") if m else None


def _no_edit_first_run(a, task, mine, last):
    """True for the first dev run of the task that ESCALATEd with zero edits: the job worktree exists, has no commit against its base and no uncommitted file."""
    if last["verdict"] != ESCALATE or sum(1 for e in mine if e["ev"] == EV_DISPATCH and e["role"] == "dev") != 1:
        return False
    return bool(job_heads(a, task["id"])) and not job_diff(a, task["id"]) and not job_dirty(a, task["id"])


def straight_to_sonnet(a, task, mine):
    """The dev ESCALATE report that ends `mine` when it goes straight to the Sonnet dev (a PLAN GAP, or a first run with no edit), else None: no more Haiku tries, no judge gate."""
    last = mine[-1] if mine else None
    if last and last["ev"] == EV_REPORT and last.get("role") == "dev" and (_plan_gap(last) or _no_edit_first_run(a, task, mine, last)):
        return last
    return None


def _after_dev_other(a, task, mine, rid, s, last):
    if (
        why := _no_fix(last)
    ) is not None:  # no ladder, no retry: the owner decides once (wait for upstream, accept the risk with `orch.py skip`, or change the brief)
        return {
            "action": "ask_main",
            "message": f"{task['id']}: NO FIX, owner decision: {why}; report {last['file']}. No further tries run. "
            "Decide: wait for the cause to be fixed (then `orch.py retry`), accept the risk (`orch.py skip`), or change the brief." + hint(a, task["id"]),
        }
    if last["verdict"] == ESCALATE and not task["owner_sonnet"] and not s.sonnet_dev:
        sonnet = s.dev_esc > 1 or straight_to_sonnet(a, task, mine) is not None  # Haiku ESCALATE counts as a failed try: one more Haiku try, then Sonnet
        return _disp(a, task, rid, "dev", sonnet, rework=last["file"])
    if _plan_gap(last):  # Sonnet (or an owner-Sonnet task) also found a plan gap: the lead fixes the plan
        return {
            "action": "ask_main",
            "message": f"{task['id']}: PLAN GAP: {_gap_text(last)}; report {last['file']}" + hint(a, task["id"]),
        }
    rev = _reviewer_before(mine, last)
    if last["verdict"] == BLOCKED and rev and not job_diff(a, task["id"]):
        # the dev could not act on the reviewer's finding and left no change: another reviewer round would only repeat it
        return {
            "action": "ask_main",
            "message": f"{task['id']}: dev BLOCKED on the reviewer finding with no change ({last.get('reason', '')}); no further review round. "
            f"Decide: the finding {rev['file']} is out of scope (file it in the backlog, then `orch.py skip`) or fix it by hand; report {last['file']}"
            + hint(a, task["id"]),
        }
    return {
        "action": "ask_main",
        "message": f"{task['id']}: dev verdict {last['verdict']} ({last.get('reason', '')}); report {last['file']}" + hint(a, task["id"]),
    }


def _gap_text(last):
    m = re.search(r"^\s*(?:VERDICT:\s*)?ESCALATE: PLAN GAP ?[:\-\u2013\u2014]\s*(.*)$", read_text(last["file"]), re.MULTILINE)
    return (m.group(1) if m else "")[:300]


def _reviewer_before(mine, dev_report):
    """The reviewer FAIL/ESCALATE report that the dev `dev_report` was sent to rework, else None."""
    i = next((k for k, e in enumerate(mine) if e is dev_report), 0)
    rep = next((e for e in reversed(mine[:i]) if e["ev"] == EV_REPORT and e.get("role") == "reviewer"), None)
    return rep if rep and rep["verdict"] in (FAIL, ESCALATE) else None


def _after_script(a, task, mine, rid, s, last):
    if last["id"] in script_ids(
        mine, "escalation gate"
    ):  # the gate only guards the step before it: plan that step again without the gate events (`_gate` then reads the verdict)
        return _next_step(a, task, [e for e in mine if e.get("id") != last["id"]], rid)
    if last["id"] in s.test_ids:  # the test run: BLOCKED -> lead; FAIL -> dev ladder like an ESCALATE; DONE -> pre-review / reviewer
        return _after_test(a, task, mine, rid, s, last)
    if last["id"] in s.gate_ids:  # the task gates: BLOCKED -> lead; FAIL -> free dev rounds, one Sonnet dev, then the lead; DONE -> quality run / review
        return _after_taskgates(a, task, mine, rid, s, last)
    if last["id"] in s.qual_ids:  # advisory: FAIL -> one dev round with the findings (Haiku, or Sonnet for an owner-Sonnet task); anything else -> review
        if last["verdict"] == FAIL and _tries(mine, "dev") < MAX_TRIES:
            return _disp(a, task, rid, "dev", task["owner_sonnet"], rework=last["file"])
        return _to_review(a, task, mine, rid, s)
    # the pre-review: FAIL -> dev reworks with its reason; SKIPPED is no review: the paid reviewer or the lead (setting), never a pass; anything else -> the paid reviewer
    if last["verdict"] == SKIPPED and prereview_skip(a) == PREREVIEW_SKIP_ASK:
        return {
            "action": "ask_main",
            "message": f"{task['id']}: local pre-review skipped, no review happened ({last.get('reason', '')}); report {last['file']}" + hint(a, task["id"]),
        }
    if last["verdict"] == FAIL:
        # not a dev try and no escalation: the dev keeps the model it had
        return _disp(a, task, rid, "dev", task["owner_sonnet"] or sonnet_dev_dispatched(mine), rework=last["file"], free=True)
    return _disp_reviewer(a, task, rid, s, mine)


def _after_test(a, task, mine, rid, s, last):
    if last["verdict"] == BLOCKED:
        return {"action": "ask_main", "message": f"{task['id']}: Test lines cannot run (infrastructure); output {last['file']}" + hint(a, task["id"])}
    if last["verdict"] == FAIL:
        repo = getattr(a, "dir", ".")
        cls, why = classify_test_run(read_text(last["file"]), os.path.join(repo, WORK_DIR, a.stage), task["id"], repo)
        if cls in STOP:  # no dev retry and no Sonnet can fix a wrong Test line or a broken environment
            return {"action": "ask_main", "message": f"{task['id']}: test run failed, class {cls}: {why}; output {last['file']}" + hint(a, task["id"])}
        if s.sonnet_dev or (task["owner_sonnet"] and s.test_fails > 1):  # Sonnet tried already: the lead decides
            return {"action": "ask_main", "message": f"{task['id']}: Test lines still red after Sonnet; output {last['file']}" + hint(a, task["id"])}
        sonnet = s.test_fails >= 2 or task["owner_sonnet"]
        return _disp(a, task, rid, "dev", sonnet, rework=last["file"])
    return _quality_or_review(a, task, mine, rid, s)


def _taskgate_fails(a, task, mine):
    """Red task-gate reports of the task over the whole event log (an auto-retry does not reset the count), oldest first."""
    full = [e for e in (getattr(a, "orch_events", None) or mine) if e.get("task") == task["id"]]
    ids = script_ids(full, "task gates")
    return [e for e in full if e["ev"] == EV_REPORT and e["id"] in ids and e["verdict"] == FAIL]


def _taskgates_all(a, task, rid, fails):
    """One rework file with the output of every red gate run, for the Sonnet dev."""
    f = work_path(os.path.join(getattr(a, "dir", "."), WORK_DIR, a.stage), f"taskgates-all-{task['id']}-{rid}.md", create=True)
    write_text(f, "".join(f"## gate run {i}\n{read_text(e['file'])}\n" for i, e in enumerate(fails, 1)))
    return f


def _after_taskgates(a, task, mine, rid, s, last):
    if last["verdict"] == BLOCKED:
        return {"action": "ask_main", "message": f"{task['id']}: task gates cannot run; output {last['file']}" + hint(a, task["id"])}
    if last["verdict"] != FAIL:
        return _quality_or_review(a, task, mine, rid, s)
    fails = _taskgate_fails(a, task, mine)
    sonnet = task["owner_sonnet"] or s.sonnet_dev
    if len(fails) <= GATE_FREE_ROUNDS:  # free rounds: no dev try, the dev keeps its model
        return _disp(a, task, rid, "dev", sonnet, rework=last["file"], free=True)
    if sonnet:  # red after the Sonnet rework: the lead decides
        return {"action": "ask_main", "message": f"{task['id']}: task gates still red after Sonnet; output {last['file']}" + hint(a, task["id"])}
    return _disp(a, task, rid, "dev", True, rework=_taskgates_all(a, task, rid, fails))


def _after_reviewer(a, task, mine, rid, s, last):
    if last["verdict"] == ESCALATE and not task["review_sonnet"] and not sonnet_review_dispatched(mine):
        return _disp(a, task, rid, "reviewer", True)  # a Haiku reviewer ESCALATE goes straight to the Sonnet reviewer
    if last["verdict"] in (FAIL, ESCALATE):
        if s.rounds > MAX_ROUNDS - 1:
            return {"action": "ask_main", "message": f"{task['id']}: review failed {s.rounds} rounds; findings {last['file']}" + hint(a, task["id"])}
        return _disp(a, task, rid, "dev", True, rework=last["file"])  # owner 2026-10-29: Haiku dev+review, one FAIL -> Sonnet dev+review, a second FAIL -> lead
    return None


def _tries(mine, role, model=None):
    return sum(1 for e in mine if e["ev"] == EV_DISPATCH and e["role"] == role and not e.get("free") and (model is None or e["model"] == model))


def next_step(a, task, mine, rid):
    """-> dispatch dict, ask_main dict, or None (task passed) for one task, from its events. Cap: at most MAX_TRIES runs of the dev and of the reviewer
    per task (any model); a fifth one goes to the lead."""
    r = _gate(a, task, mine, rid, _next_step(a, task, mine, rid))
    if r and r.get("action") == "dispatch" and r["agent"] in ("dev", "reviewer") and _tries(mine, r["agent"]) >= MAX_TRIES:
        return {"action": "ask_main", "message": f"{task['id']}: {MAX_TRIES} {r['agent']} tries used" + hint(a, task["id"])}
    return r


GATE_FAILURES = 3


def _paid_dev_retry(r):
    """True for the step that starts paying for Sonnet on the dev ladder: the Sonnet dev."""
    return bool(r) and r.get("action") == "dispatch" and r.get("agent") == "dev" and r.get("model") == MODEL_SONNET


def _after_task_gates_red(mine):
    """True when the last event is a task-gates report: the judge reads dev/reviewer/test failures, not gate output."""
    return bool(mine) and mine[-1].get("id") in script_ids(mine, "task gates")


def _judge_applies(a, task, mine, r):
    """The judge reads only a failure that goes to the Sonnet dev: not gate output, a PLAN GAP or a first-run no-edit ESCALATE."""
    return gate_on(a) and _paid_dev_retry(r) and not _after_task_gates_red(mine) and not straight_to_sonnet(a, task, mine)


def _gate(a, task, mine, rid, r):
    """One-way veto before the Sonnet dev: the local judge reads the failure once; a FAIL (no model can fix it by editing) sends the task to the lead, anything else leaves `r` as it was."""
    f = failed_event(mine) if _judge_applies(a, task, mine, r) else None
    if not f:
        return r
    after = _events_after(mine, f)
    gate = next((e for e in after if e["ev"] == EV_DISPATCH and (e.get("why") or "").startswith("escalation gate")), None)
    if gate is None:
        return _gate_script(a, task, mine, rid)
    rep = next((e for e in after if e["ev"] == EV_REPORT and e["id"] == gate["id"]), None)
    if rep and rep["verdict"] == FAIL:
        return _gate_veto(a, task, f, rep)
    return r


def _events_after(mine, f):
    return mine[max(i for i, e in enumerate(mine) if e is f) + 1 :]


def _gate_files(a):
    """-> the optional judge inputs as flags: the stage's plan-notes.md (the feature dir of a phase stage) and the project repo's AGENTS.md; a missing file is left out."""
    sd = resolve_stage(getattr(a, "dir", "."), a.stage, strict=False, phases=True)
    feat = os.path.dirname(os.path.dirname(sd)) if os.path.basename(os.path.dirname(sd)) == PHASES_DIR else sd
    notes = os.path.join(feat, PLAN_NOTES_FILE)
    rules = os.path.join(repo_dir(getattr(a, "dir", "."), project(getattr(a, "dir", "."))), RULES_FILE)
    return "".join(f" {flag} {path}" for flag, path in (("--notes", notes), ("--rules", rules)) if os.path.isfile(path))


def _gate_script(a, task, mine, rid):
    return _script(
        task,
        rid,
        "escalation gate (lp judge, one-way veto); FAIL = no stronger model can fix this, the task goes to the lead",
        f"{tool_cmd('localmodel/escalation_gate.py')} --stage {a.stage} --task {task['id']} --dir {getattr(a, 'dir', '.')} "
        + " ".join(
            f"--failure {e['file']}" for e in failing_reports(mine)[-GATE_FAILURES:]
        )  # the last few reds, oldest first: the last is the live failure, the others only context
        + _gate_files(a),
    )


GATE_VERDICT_RE = re.compile(r"^judge FAIL(?: \[(\w+)\])?: ?(.*)$")


def _gate_veto(a, task, f, rep):
    line = [*read_text(rep["file"]).split("\n", 2), "", ""][1]
    m = GATE_VERDICT_RE.match(line)
    kind, why = (m[1] or "", m[2]) if m else ("", line)
    what = (
        f"plan error: {why.removeprefix('plan error:').strip()[:300]}; the brief must change"
        if kind == GATE_PLAN_ERROR
        else f"escalation vetoed by the local judge ({(kind + ': ') if kind else ''}{why[:300] or 'no reason'})"
    )
    return {"action": "ask_main", "message": f"{task['id']}: {what}; failure {f['file']}, gate {rep['file']}" + hint(a, task["id"])}


RETRY_ESCALATE_WHY = "retry by lead after ESCALATE: Sonnet"


def _lead_retry_after_escalate(a, task):
    """True when the task's last retry marker is the lead's (not auto, script or review) and the last dev report before it was an ESCALATE: history() hid that, the dev goes to Sonnet."""
    raw = [e for e in (getattr(a, "orch_events", None) or []) if e.get("task") == task["id"]]
    i = next((k for k in range(len(raw) - 1, -1, -1) if raw[k]["ev"] == EV_RETRY), None)
    if i is None or i != len(raw) - 1 or raw[i].get("auto") or raw[i].get("from") in ("script", "review"):
        return False
    rep = next((e for e in reversed(raw[:i]) if e["ev"] == EV_REPORT and e.get("role") in ("dev", "reviewer")), None)
    return rep is not None and rep["role"] == "dev" and rep["verdict"] == ESCALATE


def is_webtest(a, task):
    """True when the task file says `Type: webtest`: a browser check. The web-tester agent runs it (a dev cannot drive a browser); there is no job worktree, no review and no merge."""
    repo = getattr(a, "dir", ".")
    return task_type(read_text(work_path(os.path.join(repo, WORK_DIR, a.stage), task["id"] + ".md"))) == "webtest"


def _next_webtest(a, task, mine, rid):
    """The steps of a `Type: webtest` task: web-tester (PASS) -> the task's Test lines by script (they validate its report) -> done. Anything else goes to the lead once."""
    if any(e["ev"] == EV_SKIP for e in mine):
        return None
    last = mine[-1] if mine else None
    if last is None:
        d = _disp(a, task, rid, WEB_TESTER, False)
        d["prompt"] = webtest_prompt(a, task, rid)
        d["why"] = "browser task (Type: webtest): the web-tester runs it, the task's Test lines check its report"
        return d
    if last["ev"] == EV_DISPATCH:
        return {"action": "ask_main", "message": f"{last['id']} ({last['agent']} on {task['id']}) was dispatched but never reported" + hint(a, task["id"])}
    if last["role"] == WEB_TESTER:
        if last["verdict"] != PASS:
            return {
                "action": "ask_main",
                "message": f"{task['id']}: web-tester verdict {last['verdict']} ({last.get('reason', '')}); report {last['file']}" + hint(a, task["id"]),
            }
        if testrun_on(a) and not script_ids(mine, "test run"):
            return _test_run_script(a, task, rid, "test run (the task's Test lines check the web-tester's report on the stage branch checkout)")
        return None
    if last["verdict"] in (DONE, SKIPPED):
        return None
    return {
        "action": "ask_main",
        "message": f"{task['id']}: the Test lines failed on the web-tester's report; output {last['file']}" + hint(a, task["id"]),
    }


def _next_step(a, task, mine, rid):
    if is_webtest(a, task):
        return _next_webtest(a, task, mine, rid)
    if any(e["ev"] == EV_SKIP or (e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] == PASS) for e in mine):
        return None
    last = mine[-1] if mine else None
    s = _TaskState(a, task, mine)
    if last is None:
        if _lead_retry_after_escalate(a, task):
            d = _disp(a, task, rid, "dev", True)
            d["why"] = RETRY_ESCALATE_WHY
            return d
        return _disp(a, task, rid, "dev", task["owner_sonnet"])
    if last["ev"] == EV_DISPATCH:
        return {"action": "ask_main", "message": f"{last['id']} ({last['agent']} on {task['id']}) was dispatched but never reported" + hint(a, task["id"])}
    if last["role"] == "dev":
        return _after_dev(a, task, mine, rid, s, last)
    if last["role"] == "script":
        return _after_script(a, task, mine, rid, s, last)
    return _after_reviewer(a, task, mine, rid, s, last)  # last was a reviewer report


def gate_on(a):
    return bool(project(getattr(a, "dir", ".")).get("gate", True))


def prereview_skip(a):
    """What a skipped local pre-review leads to: env ENV_PREREVIEW_SKIP, else the `prereview_skip` setting; an unknown value means the paid reviewer."""
    v = os.environ.get(ENV_PREREVIEW_SKIP) or project(getattr(a, "dir", ".")).get("prereview_skip")
    return v if v == PREREVIEW_SKIP_ASK else PREREVIEW_SKIP_REVIEWER


def task_gates_on(a):
    return driver_gates_on(project(getattr(a, "dir", ".")))


def prereview_on(a):
    return bool(project(getattr(a, "dir", ".")).get("prereview", False))


def sonnet_dev_dispatched(mine):
    return any(e["ev"] == EV_DISPATCH and e["role"] == "dev" and e["model"] == MODEL_SONNET for e in mine)


def sonnet_review_dispatched(mine):
    return any(e["ev"] == EV_DISPATCH and e["role"] == "reviewer" and e["model"] == MODEL_SONNET for e in mine)


def step(sid, agent, model, prompt, ok, effort=None, why=""):
    return {"id": sid, "agent": agent, "model": model, "prompt": prompt, "ok": ok, "effort": effort, "why": why}


ALWAYS_SCRIPT = ("cleanup",)  # needs no ENV.md and no model: the ops session failed twice on a missing .work/<feature>/ENV.md


def ops_as_script(a, cfg, st, sub):
    """The ops step ST as a no-model script step when pipeline.json `ops_scripts` lists its id or the step is in ALWAYS_SCRIPT (ops_steps.py SUB); otherwise ST unchanged (the old ops session)."""
    if st["id"] not in ALWAYS_SCRIPT and st["id"] not in (cfg.get("ops_scripts") or []):
        return st
    cmd = f"{tool_cmd('ops_steps.py')} {sub} --dir {os.path.abspath(a.dir)} --stage {a.stage}"
    return step(st["id"], "script", None, cmd, st["ok"], None, st["why"] + " (by script)")


def _phase_dispatch(st, rid):
    return {
        "id": rid,
        "agent": st["agent"],
        "model": st["model"],
        "task": st["id"],
        "role": st["agent"],
        "effort": st["effort"],
        "why": st["why"],
        "prompt": st["prompt"].replace("{rid}", rid),
    }


def _phase_failed_ask(a, st, rep):
    return (
        f"{st['id']}: {st['agent']} verdict {rep['verdict']} ({rep.get('reason', '')}); report {rep['file']}"
        + (f"; tools missing, see .work/{a.stage}/audits/tools-check.md; lead: retry or skip --step tools-check" if st["id"] == "tools-check" else "")
        + hint(a, st["id"])
    )


def run_phase(a, evs, steps, n):
    """Parallel steps. -> dispatch dict (steps not yet satisfied and not failed), ask_main dict, or None when every step is satisfied."""
    ds: list = []
    asks: list = []
    for st in steps:
        mine = history(evs, st["id"])
        if st.get("pause"):  # lead action: satisfied only by `orch.py skip --step <id>`
            if not any(e["ev"] == EV_SKIP for e in mine):
                asks.append(st["prompt"])
            continue
        reps = [e for e in mine if e["ev"] == EV_REPORT]
        if any(e["ev"] == EV_SKIP for e in mine) or (reps and reps[-1]["verdict"] in st["ok"]):
            continue
        if not mine:
            ds.append(_phase_dispatch(st, f"a{n + 1 + len(ds)}"))
        elif mine[-1]["ev"] == EV_DISPATCH:
            asks.append(f"{st['id']} ({st['agent']}) was dispatched but never reported" + hint(a, st["id"]))
        else:
            asks.append(_phase_failed_ask(a, st, reps[-1]))
    if ds:
        return {"action": "dispatch", "dispatches": ds}
    if asks:
        return {"action": "ask_main", "message": "\n".join(asks)}
    return None


def last_verdict(evs, sid):
    reps = [e for e in history(evs, sid) if e["ev"] == EV_REPORT]
    return reps[-1]["verdict"] if reps else None
