"""Pure-Python wave driver: replaces the lead session. Loops `orch.py next`/`report`, runs each dispatch as a fresh headless claude session.

    cs-wave run --stage S [--dir .] [--max-waves 12] [--max-dispatches 60] [--max-budget-usd N] [--dry-run [--stub task-1:dev=BLOCKED]]
    cs-wave status|metrics --stage S [--dir .]
    cs-wave feature --feature F [--dir .] [--dry-run]   a big plan, phase after phase (pipeline/cli_feature.py)

Env: WAVE_CLAUDE_FLAGS (default: acceptEdits plus the Bash allowlist in claude.py DEFAULT_FLAGS; a test command with any other prefix is blocked in headless sessions), WAVE_TIMEOUT (default 1800 s per session).
Limits, all optional: --max-dispatches (Sonnet and Opus sessions; default from the task list, pipeline/limits.py), --max-waves (waves with such a session; default off), --max-sessions (loop guard over all model sessions; default 60 + 8 per task). Haiku and scripts are free. Exit: 0 done, 3 limit hit, 4 blocked: back to the lead (.work/S/BLOCKED.md). Metrics: .work/S/metrics.jsonl (append-only).
"""

import argparse
import contextlib
import fcntl
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

from . import driver, gitx, graph, lead_context, limits, metrics, pr_ops, progress
from . import preflight as env_preflight
from .branches import dirty_block, ensure_work_branch, red_base_block
from .briefs import ENV_DRY_NO_CCN
from .configuration import (
    BLOCKED_FILE,
    CHECKOUT_LOCK,
    CLEAN,
    DONE,
    EV_DISPATCH,
    EV_REPORT,
    EV_RETRY,
    EV_SKIP,
    FAIL,
    PASS,
    RERUN_FROM,
    STEP_OK,
    TASKS_FILE,
    TOOLS_DIR,
    WAVE_STEP_CAP,
    WAVE_UNLIMITED,
    WORK_DIR,
    project,
    repo_path,
    tool_cmd,
)
from .ctx import Ctx
from .run import Run
from .tasks import parse_tasks
from .util import add_subcommand, append_event, read_jsonl, read_text, swallowed, work_path, write_text
from .worktrees import review_recover


def is_heavy(d):
    return limits.is_heavy(d["agent"], d.get("model"))


STUB_VERDICT = {"reviewer": PASS, "tester": PASS, "web-tester": PASS, "sec-auditor": CLEAN, "arch-auditor": CLEAN, "ux-reviewer": CLEAN}


def _dry_copy(a):
    """-> (scratch dir, Ctx on it), or None when the stage dir does not exist."""
    top = a.stage.split("/")[0]  # a phase `F/phases/NN-x`: copy the whole feature folder (Plan.md and the sibling phases decide who is audited)
    src = os.path.join(a.dir, WORK_DIR, top)
    if not os.path.isdir(os.path.join(a.dir, WORK_DIR, a.stage)):
        print(f"no such stage dir: {os.path.join(a.dir, '.work', a.stage)}")
        return None
    tmp = tempfile.mkdtemp(prefix="wave-dry-")  # the caller removes it
    shutil.copytree(src, os.path.join(tmp, WORK_DIR, top), ignore=shutil.ignore_patterns("wave.lock"))
    for f in os.listdir(a.dir):
        if (f.endswith(".md") or f == "pipeline.json") and os.path.isfile(os.path.join(a.dir, f)):
            shutil.copy(os.path.join(a.dir, f), tmp)
    return tmp, Ctx(Run.of(a).at(dir=tmp))


def _stub_plan(a):
    """--stub TASK:ROLE=V1[,V2...] (repeatable) -> {(task, role): [verdicts]}, built once per walk."""
    return Run.of(a).stub_plan


def _dry_answer(a, c, tmp, d, steps):
    """Print one dispatch and answer it with its stub verdict."""
    print(
        f"step {steps} {d['id']} {d['agent']}/{d['model'] or '-'}"
        + (f"/{d['effort']}" if d.get("effort") else "")
        + (f"  {d['prompt'].replace(TOOLS_DIR, '<tools>')[:110]}" if d["agent"] == "script" else "")
    )
    verdict = DONE if d["agent"] == "script" else STUB_VERDICT.get(d["agent"], DONE)
    task = next(
        (e.get("task") for e in read_jsonl(c.log) if e["ev"] == EV_DISPATCH and e["id"] == d["id"]), None
    )  # `orch next` strips the task from the dispatch
    forced = _stub_plan(a).get((task, d["agent"]))
    if forced:  # --stub: this task's role answers the listed verdicts in order, the last one repeats
        verdict = forced.pop(0) if len(forced) > 1 else forced[0]
    write_text(work_path(c.w, f"report-{d['id']}.md", create=True), f"VERDICT: {verdict}\n")
    driver.orch_inproc("report", a.stage, tmp, "--id", d["id"])


@contextlib.contextmanager
def _dry_env():
    """Set ENV_DRY_NO_CCN for the walk; the orch subprocesses inherit it. Restored after."""
    old = os.environ.get(ENV_DRY_NO_CCN)
    os.environ[ENV_DRY_NO_CCN] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(ENV_DRY_NO_CCN, None)
        else:
            os.environ[ENV_DRY_NO_CCN] = old


def dry_walk(a):
    """--dry-run: walk the whole stage in a scratch copy of .work/S (and the top-level *.md and pipeline.json), answering every dispatch with a stub verdict. Prints one line per dispatch. Nothing real runs, the real state is untouched."""
    a = Run.of(a)
    copied = _dry_copy(a)
    if copied is None:
        return 1
    tmp, c = copied
    try:
        with _dry_env():
            return _dry_loop(a, tmp, c)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _dry_loop(a, tmp, c):
    n = wave = steps = 0  # n, wave: model sessions only (as in the real run); steps bounds the loop whatever the plan does
    while n < (a.max_dispatches or WAVE_UNLIMITED) and wave < (a.max_waves or WAVE_UNLIMITED) and steps < WAVE_STEP_CAP:
        r = driver.orch_inproc("next", a.stage, tmp)
        act = r.get("action")
        if act != "dispatch":
            print(f"{act}: {r.get('message', '')[:200]}")
            return 0
        steps += 1
        wave += any(d["agent"] != "script" and is_heavy(d) for d in r["dispatches"])
        for d in r["dispatches"]:
            n += d["agent"] != "script" and is_heavy(d)
            _dry_answer(a, c, tmp, d, steps)
    print(f"limit: {n} Sonnet/Opus dispatches, {wave} waves with one, {steps} steps")
    return 3


def recover_unreported(c):
    """A dispatch with no report and no later retry/skip/dispatch of its task means the driver died while it ran: give it ONE automatic re-run (per task and role) instead of asking the lead. -> number re-run."""
    evs = read_jsonl(c.log)
    reported = {e["id"] for e in evs if e["ev"] == EV_REPORT}
    n = 0
    for i, e in enumerate(evs):
        if e["ev"] != EV_DISPATCH or e["id"] in reported or any(x.get("task") == e["task"] and x["ev"] in ("retry", "skip", "dispatch") for x in evs[i + 1 :]):
            continue
        if any(x["ev"] == EV_RETRY and x.get("stale") and x.get("task") == e["task"] and x.get("role") == e.get("role") for x in evs):
            continue  # already re-run once: now it is the lead's call
        append_event(
            c.log,
            {
                "ev": "retry",
                "task": e["task"],
                "role": e.get("role", ""),
                "stale": True,
                "id": e["id"],
                "note": "the driver stopped while this was running: one automatic re-run",
                **RERUN_FROM.get(e.get("role"), {}),
            },
        )
        n += 1
    return n


_HELD: dict[str, int] = {}  # lock path -> open fd; the kernel lock lives as long as the fd, so a dead driver never leaves a stale lock


def _take_lock(lock):
    """-> True when this process now holds the lock `lock` (flock on the open file). The kernel drops it when the process dies, so there is no stale-lock check, no pid reuse and no takeover guard.
    The file holds the pid for humans. A file that was removed between our open and our lock (the holder released it) is not the lock any more: open again."""
    while True:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # EWOULDBLOCK: another open file description holds it (another driver, or this process already)
            os.close(fd)
            return False
        try:
            same = os.fstat(fd).st_ino == os.stat(lock).st_ino
        except FileNotFoundError:
            same = False
        if not same:
            os.close(fd)
            continue
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        _HELD[lock] = fd
        return True


def _release_lock(lock):
    """Give the lock back: remove the file while the lock is held, then close (drops the flock). A lock this process does not hold is left alone."""
    fd = _HELD.pop(lock, None)
    if fd is None:
        return
    with contextlib.suppress(OSError):
        os.remove(lock)
    os.close(fd)


def cmd_run(a):
    a = Run.of(a)  # built once here; the whole run path (branches, Ctx, loop, limits) reads the frozen state
    if a.dry_run:
        return dry_walk(a)
    c = Ctx(a)
    os.makedirs(c.w, exist_ok=True)
    lock = os.path.join(c.w, "wave.lock")  # one driver per stage: an unreported dispatch at `next` means its session died
    if not _take_lock(lock):
        print(f"blocked: wave.py already runs this stage ({lock})")
        return 4
    try:
        checkout = _checkout_lock(a)  # one driver per main checkout: stages on the same repo switch its branch
        if not _take_lock(checkout):
            print(f"blocked: another stage runs in {repo_path(a.dir)}")
            return 4
        try:  # the locks are held before the guards: a second driver is refused before it runs gates, the sweep or a branch switch
            return _run_locked(a, c)
        finally:
            _release_lock(checkout)
    finally:
        _release_lock(lock)


def _checkout_lock(a):
    """-> the lock file of the main checkout that a stage switches branches in. A git checkout keeps it in its git dir, shared by every stage on that repo; otherwise it sits in the workspace's WORK_DIR."""
    repo = repo_path(a.dir)
    if os.path.exists(os.path.join(repo, ".git")):
        return os.path.join(gitx.out(repo, "rev-parse", "--absolute-git-dir"), CHECKOUT_LOCK)
    return os.path.join(a.dir, WORK_DIR, CHECKOUT_LOCK)


def _run_locked(a, c):
    """The stage run under the stage lock taken by cmd_run: guards, recovery, then the loop."""
    if msg := dirty_block(a):
        print(msg)
        return 4
    if msg := red_base_block(a):
        print(msg)
        return 4
    pr_ops.sweep_before_branch(a)
    if msg := ensure_work_branch(a, a.stage):
        print(msg)
        return 4
    # a block from an earlier run is stale: a dev reading it would think its dependencies are blocked
    Path(os.path.join(c.w, BLOCKED_FILE)).unlink(missing_ok=True)
    n = recover_unreported(c)
    if n:
        print(f"recovered {n} step(s) that were running when the driver stopped: they run again once")
    from search.strata import preflight

    preflight()  # one probe; if the model is down it sets STRATA=0: `lp find` uses keyword/symbol search, local-model steps skip
    if bad := review_recover(a):  # under the lock only: a crashed run left a reviewer's mutations in a job worktree
        print("blocked: review reset failed: " + "; ".join(bad))
        return 4
    if problems := _env_preflight(a, c):
        for msg, fix in problems:
            print(f"blocked: {msg} (fix: {fix})")
        print(f"blocked: env preflight failed, see {os.path.join(c.w, BLOCKED_FILE)}")
        return 4
    return run_loop(a, c)


def _env_preflight(a, c):
    """-> the (message, fix) env problems of the stage's task files; preflight.run writes them to BLOCKED.md. A probe that raises is logged and the run goes on."""
    try:
        return env_preflight.run(repo_path(a.dir), c.w)
    except Exception as ex:
        swallowed("cli_wave.env_preflight", ex)
        return []


def _limit_hit(a, c, ds):
    """-> the 'limit: ...' message when this wave would break a limit, else None. Sets c.limit."""
    model = [d for d in ds if d["agent"] != "script"]
    heavy = [d for d in model if is_heavy(d)]  # only Sonnet and Opus sessions are limited; Haiku and scripts are cheap
    if heavy and a.max_waves and c.mwave + 1 > a.max_waves:
        return f"limit: max-waves {a.max_waves} (waves with a Sonnet or Opus session)"
    c.limit = a.max_dispatches or limits.stage_limit(
        os.path.join(c.w, TASKS_FILE), project(a.dir)
    )  # not given: fixed part + multiplier per task (pipeline/limits.py)
    if c.disp + len(heavy) > c.limit:
        return f"limit: {c.limit} Sonnet and Opus sessions ({'--max-dispatches' if a.max_dispatches else 'from the task list, pipeline/limits.py'})"
    guard = a.max_sessions or limits.session_guard(os.path.join(c.w, TASKS_FILE))
    if c.sessions + len(model) > guard:
        return f"limit: {guard} model sessions in all (loop guard)"
    return None


def _run_wave(c, ds):
    model = [d for d in ds if d["agent"] != "script"]
    heavy = [d for d in model if is_heavy(d)]
    c.wave += 1
    c.disp += len(heavy)
    c.mwave += bool(heavy)
    c.sessions += len(model)
    print(progress.status_line(c, ds), flush=True)
    driver.do_wave(c, ds)


ASK_STEP_RE = re.compile(r"^(?P<step>[\w.-]+): (?P<cause>.+)$")  # "merge-w1: FAIL but no conflict ..." -> step, cause
ASK_PATH_RE = re.compile(r"\S+\.md\b")  # a report file named in the ask
ASK_CMD_RE = re.compile(r"`([^`]*\bretry --step \S+)`")  # a command the ask already suggests
ASK_TAIL_LINES = 5  # last lines of the named report shown as the error tail
ASK_REVIEW_MARK = "review failed"  # an ask cause that carries the reviewer's findings
ASK_FINDINGS_LINES = 30  # report lines shown for such an ask (the findings and their fixes)
ASK_NOTE_HINT = ' --note "<quote the reviewer\'s exact fix text>"'  # a retry after two failed reviews must carry the fix text
ASK_ESCAPE_CATEGORIES = ("Cc", "Cf")  # unicode categories shown escaped in an error tail: control (Cc) and format, zero-width and bidi (Cf)


def _ask_entries(msg):
    """-> [(step, cause, first raw line, count)] per repeated identical ask (same step and cause, paths ignored), in order."""
    seen: dict[tuple[str, str], list] = {}
    for line in (x.strip() for x in msg.splitlines()):
        if not line:
            continue
        m = ASK_STEP_RE.match(line)
        step, cause = (m["step"], m["cause"]) if m else ("", line)
        key = (step, ASK_PATH_RE.sub("<path>", cause))
        if key in seen:
            seen[key][3] += 1
        else:
            seen[key] = [step, cause, line, 1]
    return [tuple(e) for e in seen.values()]


def _escape_nonprintable(text):
    """Show zero-width, bidi and control characters (tab kept) as \\uXXXX escapes; printable text unchanged."""
    return "".join(f"\\u{ord(ch):04X}" if ch != "\t" and unicodedata.category(ch) in ASK_ESCAPE_CATEGORIES else ch for ch in text)


def _ask_tail(line):
    """Last lines of the report file the ask names (first one that exists), or a note when it names none."""
    count = ASK_FINDINGS_LINES if ASK_REVIEW_MARK in line else ASK_TAIL_LINES
    for path in ASK_PATH_RE.findall(line):
        if os.path.isfile(path):
            tail = [x for x in read_text(path).splitlines() if x.strip()][-count:]
            return f"error tail, {path}:\n" + "\n".join(f"    {_escape_nonprintable(x)}" for x in tail)
    return "error tail: none, the ask names no report file"


def _ask_text(a, msg):
    """Each ask with its cause, error tail and suggested command; a repeated identical ask is one entry with a count."""
    out = []
    for step, cause, line, count in _ask_entries(msg):
        head = f"{step}: {cause}" if step else cause
        out.append(f"{head} (x{count})" if count > 1 else head)
        out.append("  " + _ask_tail(line))
        cmd = ASK_CMD_RE.search(line)
        if cmd:
            suggested = f"`{cmd.group(1)}`"
        elif step:
            suggested = f"`{tool_cmd('orch.py')} retry --step {step}`"
        else:
            suggested = f"`{tool_cmd('wave.py')} run --stage {a.stage}` after the fix"
        if ASK_REVIEW_MARK in cause and step:
            suggested = f"`{tool_cmd('orch.py')} retry --step {step}{ASK_NOTE_HINT}`"
        out.append(f"  suggested: {suggested}")
    return "\n".join(out)


def _blocked(a, c, r):
    """The stage is blocked: -> exit code. A block goes back to the lead (main session); no model session decides it."""
    if a.max_waves and c.mwave + 1 > a.max_waves:
        print(f"limit: max-waves {a.max_waves}")
        return 3
    msg = _ask_text(a, r.get("message", ""))
    write_text(os.path.join(c.w, BLOCKED_FILE), msg + "\n")
    metrics.total_line(c)
    print(f"BLOCKED, back to lead (also in {c.w}/BLOCKED.md):\n{msg}")
    return 4


def run_loop(a, c):
    while True:
        r = driver.orch_inproc("next", a.stage, a.dir)
        act = r.get("action")
        if act == "done":
            metrics.total_line(c)
            print(f"done: {r.get('message', '')}")
            return 0
        if act == "dispatch":
            hit = _limit_hit(a, c, r["dispatches"])
            if hit:
                print(hit)
                return 3
            _run_wave(c, r["dispatches"])
        elif act == "ask_main":
            return _blocked(a, c, r)
        else:
            print(f"unknown action: {r}")
            return 1
        if a.max_budget_usd is not None and c.cost > a.max_budget_usd:
            print(f"limit: budget {a.max_budget_usd} USD exceeded ({c.cost:.2f})")
            return 3


def _verdicts_since_reset(mine):
    """-> (reviewer verdicts, last verdict of a non-dev step) after the task's last retry/skip."""
    i = max([k for k, e in enumerate(mine) if e["ev"] in ("retry", "skip")], default=-1)
    rv = [e["verdict"] for e in mine[i + 1 :] if e["ev"] == EV_REPORT and e.get("role") == "reviewer"]
    steps = [e for e in mine[i + 1 :] if e["ev"] == EV_REPORT and e.get("role") not in ("dev", "reviewer")]  # merger/tester/audit steps have no reviewer
    return rv, steps[-1]["verdict"] if steps else ""


def _task_state(mine):
    """-> done / failed / open for one task's events."""
    rv, last = _verdicts_since_reset(mine)
    if any(e["ev"] == EV_SKIP for e in mine) or PASS in rv or last in STEP_OK:
        return "done"
    if last == FAIL or rv.count(FAIL) >= 2:
        return "failed"
    return "open"


def _task_states(evs):
    """{task: state} for every dispatched or skipped task; a merge step that the retry merge-wN-2 replaced shows as done once that retry is done."""
    state = {
        t: _task_state([e for e in evs if e.get("task") == t])
        for t in dict.fromkeys(e["task"] for e in evs if e.get("ev") in ("dispatch", "skip") and e.get("task"))
    }
    for t in list(state):
        m = re.fullmatch(r"merge-(?:fix-)?w(\d+)(-late\d*|-p\d+)?", t)
        if m and state.get(f"merge-w{m[1]}{m[2] or ''}-2") == "done":
            state[t] = "done"
    return state


def _print_planning(a):
    """The planning steps (context, local model, brief, architect, check) of the feature or stage named by --stage."""
    lines = progress.planning_lines(Ctx(a))
    if lines:
        print("planning:\n" + "\n".join(lines))


def _feature_overview(rows):
    """Top section of a feature status: tasks per phase (done/total when planned, the Plan.md estimate when not) and the overall tasks left."""
    from . import plan_files as pf

    lines, done_all, total_all, est_all = [], 0, 0, 0
    for i, (p, planned, waves, task_states) in enumerate(rows, 1):
        names = [t for _, ns in waves for t in ns]
        if planned:
            done = sum(1 for t in names if task_states.get(t) == "done")
            done_all, total_all = done_all + done, total_all + len(names)
            lines.append(f"  p{i} {p['dir']}: {done}/{len(names)} tasks done, {len(names) - done} left")
        elif pf.is_finish_dir(p["dir"]):
            lines.append(f"  p{i} {p['dir']}: closing phase, no tasks of its own")
        else:
            est_all += p["tasks"]
            lines.append(f"  p{i} {p['dir']}: not planned, ~{p['tasks'] or '?'} tasks estimated")
    left = total_all - done_all
    tail = f", plus ~{est_all} in phases not planned yet" if est_all else ""
    return f"overview: {done_all}/{total_all} planned tasks done, {left} left{tail}\n" + "\n".join(lines)


def _task_waves(text):
    """The `wave 1: ... | wave 2: ...` graph line of a Tasks file -> [(label, names)]."""
    graph = re.search(r"(?m)^wave 1:.*$", text)
    waves = []
    for w in graph[0].split("|") if graph else []:
        num, _, ids = w.partition(":")
        waves.append((num.strip(), [t.strip() for t in ids.split(",")]))
    return waves


def _feature_status(a):
    """`--stage <feature>` with a Plan.md: one line per phase (planned or not), then its task states. -> False when not a feature."""
    from . import plan_files as pf

    fa = Run(dir=a.dir, feature=a.stage)
    if not os.path.exists(pf.plan_path(fa)):
        return False
    phases = pf.read_phases(fa)
    if phases and not any(pf.is_finish_dir(p["dir"]) for p in phases):  # the driver adds it on the next run; show it now
        with contextlib.suppress(ValueError):  # phase 99 has no finish row: the driver refuses it with the reason
            phases.append({"dir": f"{pf.next_phase_no(phases):02d}-finish", "status": "todo", "tasks": 0})
    rows = []  # (phase, folder, planned, waves [(label, names)], task states)
    for p in phases:
        folder = os.path.join(pf.feature_dir(fa), "phases", p["dir"])
        planned = os.path.exists(pf.tasks_path(folder))
        waves, state = [], {}
        if planned:
            c = Ctx(Run.of(a).at(stage=pf.stage_of(a.stage, p)))
            state = _task_states(read_jsonl(c.log))
            waves = _task_waves(pf.read_text(pf.tasks_path(folder)))
        rows.append((p, planned, waves, state))
    print(_feature_overview(rows))
    _print_planning(a)
    for i, (p, planned, waves, state) in enumerate(rows, 1):
        print(
            f"p{i}/{len(rows)} {p['dir']}: {p['status']}, {'planned' if planned else 'closing phase (tests, audits, merge, push, cleanup, retro)' if pf.is_finish_dir(p['dir']) else 'not planned yet'}"
        )
        for num, names in waves:
            print(f"   {num} ({len(names)} task{'s' * (len(names) != 1)}): " + ", ".join(f"{t} {state.pop(t, 'todo')}" for t in names))
        for t, st in state.items():  # steps outside the task waves (merge-w1, ...)
            print(f"   {t} {st}")
    return True


def cmd_status(a):
    if _feature_status(a):
        return 0
    _print_planning(a)
    c = Ctx(a)
    evs = read_jsonl(c.log)
    for t, s in _task_states(evs).items():
        print(f"{t} {s}")
    try:
        lost = graph.unmerged_done_ids(a, evs, parse_tasks(os.path.join(c.w, TASKS_FILE)))
    except (RuntimeError, OSError):  # status is what a lead runs to diagnose a broken repo: a git error must not hide the task states
        lost = []
    if lost:
        print(f"unmerged (done, job branch not merged into the stage branch): {', '.join(lost)}")
    return 0


def cmd_metrics(a):
    c = Ctx(a)
    for (m, ag), g in sorted(metrics.aggregate(read_jsonl(c.metrics)).items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        print(
            f"{m}/{ag}: sessions={g['sessions']} turns={g['turns']} in={g['input']} out={g['output']} cache_read={g['cache_read']} "
            f"cache_write={g['cache_write']} cost={g['cost_usd']:.4f} seconds={g['seconds']:.0f}"
        )
    return 0


def main(argv=None, extra_commands=()):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("status", cmd_status), ("metrics", cmd_metrics)):
        p = add_subcommand(sub, name, fn)
        if name == "run":
            p.add_argument("--max-waves", type=int, default=None)
            p.add_argument("--max-dispatches", type=int, default=None)
            p.add_argument("--max-sessions", type=int, default=None)
            p.add_argument("--max-budget-usd", type=float, default=None)
            p.add_argument("--dry-run", action="store_true")
            p.add_argument(
                "--stub",
                action="append",
                default=[],
                metavar="TASK:ROLE=V1[,V2]",
                help="dry-run only: force the verdicts of one task's role, e.g. task-1:dev=BLOCKED or task-1:dev=ESCALATE,DONE",
            )
            p.add_argument("--allow-dirty", action="store_true")
    for add in extra_commands:
        add(sub)
    a = ap.parse_args(argv)
    lead_context.check()
    return a.fn(a)
