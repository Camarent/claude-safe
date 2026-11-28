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
import signal
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from . import driver, entry, gitx, graph, launcher_guard, lead_context, limits, metrics, ops_preflight, pr_ops, progress
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
    EXIT_BLOCKED,
    EXIT_LIMIT,
    FAIL,
    LOCK_RETRY_MAX,
    LOCK_RETRY_S,
    NO_TASKS_HINT,
    PASS,
    PGID_FILE_NAME,
    PLAN_FILE,
    RERUN_FROM,
    SQUASH_MSG_FILE,
    STAGE_LOCK,
    STALE_RERUN_MAX,
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
from .plan_gaps_open import open_gaps
from .run import Run
from .tasks import parse_tasks
from .util import add_subcommand, append_event, read_jsonl, read_text, swallowed, unreported, work_path, write_text
from .worktree import _unmerged, read_manifest
from .worktrees import review_recover


def is_heavy(d) -> bool:
    return limits.is_heavy(d["agent"], d.get("model"))


def order_by_model(ds) -> list:
    """-> the ready dispatches grouped by model, in first-seen model order and in their own order within a model: one model's run reuses its warm cache."""
    rank: dict = {}
    for d in ds:
        rank.setdefault(d.get("model"), len(rank))
    return sorted(ds, key=lambda d: rank[d.get("model")])


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


def dry_walk(a) -> int:
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
    print(entry.limit_line(f"{n} Sonnet/Opus dispatches, {wave} waves with one, {steps} steps"))
    return EXIT_LIMIT


def _stale_reruns(evs, task, role):
    """-> how many automatic re-runs (stale retries) this task and role already got."""
    return sum(1 for x in evs if x["ev"] == EV_RETRY and x.get("stale") and x.get("task") == task and x.get("role") == role)


def _kill_stale_sessions(w):
    """Kill the process groups listed in the stage's pgid file (sessions a dead driver left running), then remove the file. -> number killed."""
    path = os.path.join(w, PGID_FILE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            pids = [int(ln) for ln in f if ln.strip().isdigit()]
    except FileNotFoundError:
        return 0
    killed = 0
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)  # each session leads its own group (start_new_session)
            killed += 1
        except ProcessLookupError:  # already gone
            continue
        except PermissionError:  # pid reused by another user's process: not ours to kill
            continue
    Path(path).unlink(missing_ok=True)
    return killed


def recover_unreported(c) -> int:
    """A dispatch with no report and no later retry/skip/dispatch of its task means the driver died while it ran: give it an automatic re-run (per task and role, up to STALE_RERUN_MAX) instead of asking the lead. Live sessions of the dead driver are killed first. -> number re-run."""
    evs = read_jsonl(c.log)
    open_ids = set(unreported(evs))
    if not open_ids:
        return 0
    _kill_stale_sessions(c.w)
    n = 0
    for e in evs:
        if e["ev"] != EV_DISPATCH or e["id"] not in open_ids:
            continue
        if _stale_reruns(evs, e["task"], e.get("role")) >= STALE_RERUN_MAX:
            continue  # re-run as often as the setting says: then it is the lead's call
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


def _flock_ex(fd) -> bool:
    """-> True when the exclusive flock on `fd` is taken. EWOULDBLOCK may come from another driver (a real holder: refused after the retries) or from a probe (other_driver) that holds the lock shared for a moment: the retries let that probe finish."""
    for attempt in range(LOCK_RETRY_MAX + 1):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # EWOULDBLOCK: another open file description holds it (another driver, a probe, or this process already)
            if attempt == LOCK_RETRY_MAX:
                return False
            time.sleep(LOCK_RETRY_S)
        else:
            return True
    return False


def _take_lock(lock):
    """-> True when this process now holds the lock `lock` (flock on the open file). The kernel drops it when the process dies, so there is no stale-lock check, no pid reuse and no takeover guard.
    The file holds the pid for humans. A file that was removed between our open and our lock (the holder released it) is not the lock any more: open again."""
    while True:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
        if not _flock_ex(fd):
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


def other_driver(a_dir, own) -> str | None:
    """-> the stage of another live driver (its STAGE_LOCK is flock-held), else None. A probe only: a shared flock taken and dropped at once, the lock is never taken and no file is created.
    `own` (a stage or a feature) and the stages under it (`own/phases/...`) are not a rival. A lock file left by a dead driver is free: the kernel dropped its flock."""
    root = Path(a_dir, WORK_DIR)
    for lock in sorted(root.glob(f"**/{STAGE_LOCK}")):
        stage = lock.parent.relative_to(root).as_posix()
        if own and (stage == own or stage.startswith(own + "/")):
            continue
        try:
            fd = os.open(lock, os.O_RDONLY)
        except FileNotFoundError:  # released between the glob and the open
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:  # EWOULDBLOCK: a driver holds it exclusively
            return stage
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return None


def cmd_run(a) -> int:
    a = Run.of(a)  # built once here; the whole run path (branches, Ctx, loop, limits) reads the frozen state
    if a.dry_run:
        return dry_walk(a)
    c = Ctx(a)
    os.makedirs(c.w, exist_ok=True)
    lock = os.path.join(c.w, STAGE_LOCK)  # one driver per stage: an unreported dispatch at `next` means its session died
    if not _take_lock(lock):
        print(f"blocked: wave.py already runs this stage ({lock})")
        return EXIT_BLOCKED
    try:
        checkout = _checkout_lock(a)  # one driver per main checkout: stages on the same repo switch its branch
        if not _take_lock(checkout):
            print(f"blocked: another stage runs in {repo_path(a.dir)}")
            return EXIT_BLOCKED
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


def _staged_paths(repo):
    """-> the paths with any change (staged or not) in the checkout REPO."""
    return {gitx.unquote_path(ln[3:]) for ln in gitx.out(repo, "status", "--porcelain").splitlines() if ln.strip()}


def _squash_job(repo, job, dirty):
    """-> True when JOB (a manifest entry) changes exactly the files DIRTY and is not merged into its base yet."""
    branch, base = job.get("branch"), job.get("base")
    if not branch or not base:
        return False
    changed = gitx.try_out(repo, "diff", "--name-only", f"{base}...{branch}")
    if changed is None or set(changed.split()) != dirty:
        return False
    return bool(_unmerged(repo, branch, base))


def reset_squash_residue(a, c) -> list:
    """An interrupted `git merge --squash` leaves staged changes and SQUASH_MSG in the checkout's git dir. When the dirty files are exactly the diff of a manifest job not yet merged, reset the checkout (git reset --hard HEAD) and log it. Any other dirty file: the checkout is left alone. -> the reset files, [] when nothing was reset."""
    repo = repo_path(a.dir)
    if not os.path.exists(os.path.join(repo, ".git")):
        return []
    if not os.path.exists(os.path.join(gitx.out(repo, "rev-parse", "--absolute-git-dir"), SQUASH_MSG_FILE)):
        return []
    dirty = _staged_paths(repo)
    if not dirty or not any(_squash_job(repo, job, dirty) for job in read_manifest(a.dir, a.stage).values()):
        return []
    gitx.out(repo, "reset", "--hard", "HEAD")
    files = sorted(dirty)
    append_event(c.log, {"ev": "merge_residue_reset", "files": files})
    return files


def _plan_only(c):
    """-> True when the stage folder holds a Plan.md and no TASKS.md (a feature whose phases are not planned yet)."""
    return os.path.exists(os.path.join(c.w, PLAN_FILE)) and not os.path.exists(os.path.join(c.w, TASKS_FILE))


def _run_locked(a, c):
    """The stage run under the stage lock taken by cmd_run: guards, recovery, then the loop."""
    if (code := _run_guards(a, c)) is not None:
        return code
    if (code := _run_preflights(a, c)) is not None:
        return code
    _warn_open_gaps(c)
    for s in ops_preflight.later_ops(c):  # the run goes on: the lead does these after the task they wait for
        print(f"lead step {s['id']} (after {', '.join(s['after'])}): {s['text']}")
    return run_loop(a, c)


def _warn_open_gaps(c):
    """Print the plan-review gaps still open in the stage folder as a warning block; the run goes on (no block). Prints nothing when none are open."""
    gaps = open_gaps(c.w)
    if not gaps:
        return
    print(f"warning: {len(gaps)} plan-review gap line(s) still open in {c.w}:")
    for ln in gaps:
        print(f"  {ln}")


def _run_guards(a, c):
    """-> EXIT_BLOCKED when the stage cannot start (after printing why), else None. Clears stale state and recovers unreported steps."""
    if _plan_only(c):  # a feature plan with no tasks yet: the loop would say `done: no tasks` with exit 0
        print(f"blocked: no TASKS.md in {c.w}, only Plan.md: run `{NO_TASKS_HINT.format(stage=a.stage)}`")
        return EXIT_BLOCKED
    if files := reset_squash_residue(a, c):
        print(f"reset {len(files)} file(s) left by an interrupted squash merge: {', '.join(files)}")
    if msg := dirty_block(a):
        print(msg)
        return EXIT_BLOCKED
    if msg := red_base_block(a):
        print(msg)
        return EXIT_BLOCKED
    pr_ops.sweep_before_branch(a)
    if msg := ensure_work_branch(a, a.stage):
        print(msg)
        return EXIT_BLOCKED
    # a block from an earlier run is stale: a dev reading it would think its dependencies are blocked
    entry.clear_blocked(c.w)
    n = recover_unreported(c)
    if n:
        print(f"recovered {n} step(s) that were running when the driver stopped: they run again once")
    return None


def _run_preflights(a, c):
    """-> EXIT_BLOCKED when a probe, review reset, env check or manual step blocks the run (after printing why), else None."""
    from search.strata import preflight

    preflight()  # one probe; if the model is down it sets STRATA=0: `lp find` uses keyword/symbol search, local-model steps skip
    if bad := review_recover(a):  # under the lock only: a crashed run left a reviewer's mutations in a job worktree
        print("blocked: review reset failed: " + "; ".join(bad))
        return EXIT_BLOCKED
    if problems := _env_preflight(a, c):
        for msg, fix in problems:
            print(f"blocked: {msg} (fix: {fix})")
        print(f"blocked: env preflight failed, see {os.path.join(c.w, BLOCKED_FILE)}")
        return EXIT_BLOCKED
    if steps := ops_preflight.open_ops(a, c):  # once, before any dispatch: every open manual step with no task dependency
        for s in steps:
            print(f"blocked: manual step {s['id']} is open: {s['text']} (dependents: {', '.join(s['dependents']) or 'none'}; skip: {s['skip']})")
        entry.write_blocked(c.w, ops_preflight.blocked_text(steps) + "\n")
        return EXIT_BLOCKED
    return None


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
    return _dispatch_limit_msg(a, c, heavy) or _session_limit_msg(a, c, model)


def _dispatch_limit_msg(a, c, heavy):
    """-> the 'limit: ...' message when the Sonnet and Opus dispatches would pass the stage limit. Sets c.limit."""
    c.limit = a.max_dispatches or limits.stage_limit(
        os.path.join(c.w, TASKS_FILE), project(a.dir)
    )  # not given: fixed part + multiplier per task (pipeline/limits.py)
    if c.disp + len(heavy) > c.limit:
        return f"limit: {c.limit} Sonnet and Opus sessions ({'--max-dispatches' if a.max_dispatches else 'from the task list, pipeline/limits.py'})"
    return None


def _session_limit_msg(a, c, model):
    """-> the 'limit: ...' message when the model sessions would pass the loop guard, else None."""
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
        print(entry.limit_line(f"max-waves {a.max_waves}"))
        return EXIT_LIMIT
    msg = _ask_text(a, r.get("message", ""))
    entry.write_blocked(c.w, msg + "\n")
    metrics.total_line(c)
    print(entry.blocked_line(c.w, msg))
    return EXIT_BLOCKED


def run_loop(a, c) -> int:
    while True:
        r = driver.orch_inproc("next", a.stage, a.dir)
        act = r.get("action")
        if act == "done":
            metrics.total_line(c)
            print(entry.done_line(r.get("message", "")))
            return 0
        if act == "dispatch":
            hit = _limit_hit(a, c, r["dispatches"])
            if hit:
                print(hit)
                return EXIT_LIMIT
            _run_wave(c, order_by_model(r["dispatches"]))
        elif act == "ask_main":
            return _blocked(a, c, r)
        else:
            print(f"unknown action: {r}")
            return 1
        if a.max_budget_usd is not None and c.cost > a.max_budget_usd:
            print(entry.limit_line(f"budget {a.max_budget_usd} USD exceeded ({c.cost:.2f})"))
            return EXIT_LIMIT


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
    rows = [_phase_row(a, fa, p) for p in phases]  # (phase, planned, waves [(label, names)], task states)
    print(_feature_overview(rows))
    _print_planning(a)
    _print_phase_rows(rows)
    return True


def _phase_row(a, fa, p):
    """-> (phase, planned, waves, task states) for one phase of the feature: waves and states come from its TASKS.md and log when planned."""
    from . import plan_files as pf

    folder = os.path.join(pf.feature_dir(fa), "phases", p["dir"])
    planned = os.path.exists(pf.tasks_path(folder))
    waves, state = [], {}
    if planned:
        c = Ctx(Run.of(a).at(stage=pf.stage_of(a.stage, p)))
        state = _task_states(read_jsonl(c.log))
        waves = _task_waves(pf.read_text(pf.tasks_path(folder)))
    return (p, planned, waves, state)


def _phase_label(p, planned):
    """-> the planning state shown after a phase's status: planned, the closing phase, or not planned yet."""
    from . import plan_files as pf

    if planned:
        return "planned"
    if pf.is_finish_dir(p["dir"]):
        return "closing phase (tests, audits, merge, push, cleanup, retro)"
    return "not planned yet"


def _print_phase_rows(rows):
    """Print one block per phase: its status line, its task waves, then steps outside the waves."""
    for i, (p, planned, waves, state) in enumerate(rows, 1):
        print(f"p{i}/{len(rows)} {p['dir']}: {p['status']}, {_phase_label(p, planned)}")
        for num, names in waves:
            print(f"   {num} ({len(names)} task{'s' * (len(names) != 1)}): " + ", ".join(f"{t} {state.pop(t, 'todo')}" for t in names))
        for t, st in state.items():  # steps outside the task waves (merge-w1, ...)
            print(f"   {t} {st}")


def cmd_status(a) -> int:
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


def cmd_metrics(a) -> int:
    c = Ctx(a)
    for (m, ag), g in sorted(metrics.aggregate(read_jsonl(c.metrics)).items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        print(
            f"{m}/{ag}: sessions={g['sessions']} turns={g['turns']} in={g['input']} out={g['output']} cache_read={g['cache_read']} "
            f"cache_write={g['cache_write']} cost={g['cost_usd']:.4f} seconds={g['seconds']:.0f}"
        )
    return 0


def _run_entry(a, ap: argparse.ArgumentParser) -> int:
    """The one run form: `run NAME` goes to a.on_name (the feature hook wired by cli_feature.add_feature_command); `run --stage S` without /phases/ is an alias of `run S` when the hook is wired; `run --stage F/phases/NN` stays cmd_run (the driver's phase path). Without the hook only the stage form runs, as cmd_run."""
    if bool(a.name) == bool(a.stage):
        ap.error("run takes NAME or --stage STAGE, not both and not neither")
    if a.name is not None and entry.PHASES_SEP in a.name:
        ap.error("run NAME takes a feature; for a phase use --stage F/phases/NN")
    if a.name is None and entry.PHASES_SEP not in a.stage and a.on_name is not None:
        print(entry.alias_line(f"run --stage {a.stage}", f"run {a.stage}"))
        a.name, a.stage = a.stage, None
    if a.name is None:
        return cmd_run(a)
    on_name = a.on_name
    if on_name is None:
        ap.error("run NAME needs the feature entry: wire it with extra_commands")
    a.feature = a.name
    return on_name(a)


def main(argv=None, extra_commands=()) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("metrics", cmd_metrics)):
        add_subcommand(sub, name, fn)
    p = add_subcommand(sub, "run", cmd_run, stage_required=False)
    p.add_argument("name", nargs="?", metavar="NAME", help="the feature to run (the entry form); or use --stage STAGE")
    entry.add_run_flags(p)
    p.set_defaults(on_name=None)
    for add in extra_commands:
        add(sub)
    a = ap.parse_args(argv)
    lead_context.check()
    refusal = launcher_guard.check(project(a.dir), os.environ, a.cmd, getattr(a, "dry_run", False))
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    if a.cmd == "run":
        return _run_entry(a, ap)
    return a.fn(a)
