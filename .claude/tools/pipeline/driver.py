"""Wave execution: call orch.py, run each dispatch as a headless claude session, record metrics."""

import contextlib
import io
import json
import os
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from . import claude, metrics, util
from .audit_phases import save_audit_reply
from .configuration import (
    AUDIT_ESCALATE_SUFFIX,
    AUDITORS,
    ENV_AGENT_KIND,
    ENV_GATE_KIND,
    ENV_GUARD_ALLOW,
    ENV_GUARD_JOB,
    ENV_GUARD_LOG,
    ENV_GUARD_MAIN,
    ENV_GUARD_WORKTREE,
    ENV_REVIEW_DIR,
    ENV_SCRATCH_DIR,
    EV_DISPATCH,
    GATE_KIND_MUTATION,
    GUARD_LOG_FILE,
    PGID_FILE_NAME,
    REPORTS_DIR,
    REVIEWER,
    SCRATCH_DIR,
    SCRATCH_ENV,
    SCRIPT_OUTPUT_MAX,
    TOOLS_DIR,
    VERDICT_PREFIX,
    WORK_DIR,
    WORKTREE_CWD_AGENTS,
    project,
    test_env,
)
from .pr_ops import checks_timeout
from .util import read_jsonl, read_text, run_py, work_path, write_text
from .worktrees import job_info, review_begin, review_reset, review_root


def orch(cmd, stage, dir, *extra) -> dict:
    """Run orch.py, -> its parsed json line."""
    r = run_py("orch.py", cmd, "--stage", stage, "--dir", dir, *extra)
    lines = [line for line in r.stdout.splitlines() if line.strip()]
    if r.returncode or not lines:
        return {"action": "ask_main", "message": f"orch {cmd} failed: {(r.stderr or r.stdout).strip()[-200:]}"}
    try:
        return json.loads(lines[-1])
    except ValueError:  # a stray non-JSON line on stdout: the answer is lost, ask the lead instead of crashing the wave
        return {"action": "ask_main", "message": f"orch {cmd} gave no JSON: {lines[-1][:200]}"}


def orch_inproc(cmd, stage, dir, *extra) -> dict:
    """Same result as `orch`, but calls cli_orch.main in this process (no 100 ms interpreter start per call). Used by the dry run, which makes dozens of calls."""
    from . import cli_orch

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli_orch.main([cmd, "--stage", stage, "--dir", dir, *extra])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if isinstance(e.code, str):
                print(e.code, file=err)
        except Exception as e:  # the subprocess `orch` answered ask_main on any failure; keep that contract
            code = 1
            print(repr(e), file=err)
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    if code or not lines:
        return {"action": "ask_main", "message": f"orch {cmd} failed: {(err.getvalue() or out.getvalue()).strip()[-200:]}"}
    return json.loads(lines[-1])


SCRIPT_PREFIX = f"python3 -I {TOOLS_DIR}/"


def script_timeout() -> float:
    """Seconds one script step may run: env ORCH_SCRIPT_TIMEOUT, else SCRIPT_TIMEOUT_S."""
    return util.script_timeout()


def script_step_limit(cfg) -> float:
    """Driver limit of one script step: the script limit plus the PR checks wait, so a PR step reports pending checks before the driver kills it."""
    return script_timeout() + checks_timeout(cfg)


def run_script(cmd, cwd, timeout=None) -> dict:
    """A no-model step: only commands under tools/ run. timeout: this step's own limit (None = script_timeout()). -> session dict with the output as result."""
    if not cmd.startswith(SCRIPT_PREFIX):
        return dict(claude.usage({}), error=True, result=f"{VERDICT_PREFIX}FAIL\nwave: refused script {cmd[:80]}")
    limit = timeout or script_timeout()
    t0 = time.time()
    try:
        p = claude.track(
            subprocess.Popen(shlex.split(cmd), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        )  # own group: a timeout kills servers and builds the script started too
        try:
            stdout, stderr = p.communicate(timeout=limit)
        except BaseException:  # timeout, Ctrl-C: the script lives in its own session and would outlive the driver
            claude.kill_group(p)
            p.communicate()
            raise
        finally:
            claude.untrack(p)
        r = subprocess.CompletedProcess(p.args, p.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        return dict(
            claude.usage({}),
            seconds=round(time.time() - t0),
            error=True,
            result=f"{VERDICT_PREFIX}FAIL\nwave: script timed out after {limit:.0f}s: {cmd[:80]}",
        )
    except (OSError, ValueError, OverflowError) as e:  # unparsable command, missing cwd or binary, a non-finite limit: an error result, the driver goes on
        return dict(claude.usage({}), seconds=round(time.time() - t0), error=True, result=f"{VERDICT_PREFIX}FAIL\nwave: script could not run: {e}")
    out = r.stdout + r.stderr
    if len(out) > SCRIPT_OUTPUT_MAX:  # keep the VERDICT first line, the verdict parser reads only that
        first = out.split("\n", 1)[0]
        out = (first + "\n...\n" if first.startswith("VERDICT:") else "") + out[-(SCRIPT_OUTPUT_MAX - 100) :]
    return dict(claude.usage({}), seconds=round(time.time() - t0), error=bool(r.returncode), result=out)


def dispatch_task(c, d) -> str | None:
    """-> the task id of dispatch D: `orch next` strips it from the dispatch, the dispatch event in the log keeps it."""
    return d.get("task") or next((e.get("task") for e in read_jsonl(c.log) if e["ev"] == EV_DISPATCH and e["id"] == d["id"]), None)


def _auditor_turns(c, d):
    """-> the `--max-turns` of an auditor session: the larger escalation cap for the Opus rerun (step id ends with the escalate suffix), else the normal cap."""
    cfg = project(c.dir)
    return cfg.get("auditor_escalate_max_turns" if (dispatch_task(c, d) or "").endswith(AUDIT_ESCALATE_SUFFIX) else "auditor_max_turns")


def scratch_env(c) -> dict[str, str]:
    """-> env for an agent session: the stage scratch dir (created) as ENV_SCRATCH_DIR and as TMPDIR, unless pipeline.json test_env sets TMPDIR."""
    d = os.path.join(os.path.abspath(c.w), SCRATCH_DIR)
    os.makedirs(d, exist_ok=True)
    env = {ENV_SCRATCH_DIR: d}
    if SCRATCH_ENV not in test_env(project(c.dir), c.dir):
        env[SCRATCH_ENV] = d
    return env


def _dev_job(c, d):
    """-> the job worktree info of dev dispatch D (WORKTREE_CWD_AGENTS only), or None when there is none."""
    if d["agent"] not in WORKTREE_CWD_AGENTS or not project(c.dir).get("worktrees"):
        return None
    return job_info(c, dispatch_task(c, d), create=False)


def session_cwd(c, d) -> str:
    """-> the dir the session of dispatch D runs in: the job worktree root for the dev roles when worktrees are on, else the project dir. Reviewer and read-only roles keep the project dir."""
    info = _dev_job(c, d)
    return info["root"] if info else c.dir


def guard_env(c, d) -> dict[str, str]:
    """-> env that binds a dev session to its job worktree (write_guard): the one tree it may write, the main checkout denied, the reports and scratch dirs allowed. {} when there is no job worktree."""
    info = _dev_job(c, d)
    if not info:
        return {}
    task = dispatch_task(c, d) or ""
    return {
        ENV_GUARD_WORKTREE: info["root"],
        ENV_GUARD_MAIN: os.path.abspath(c.dir),
        ENV_GUARD_ALLOW: os.pathsep.join([os.path.join(c.w, REPORTS_DIR), os.path.join(c.w, SCRATCH_DIR)]),
        ENV_GUARD_JOB: task,
        ENV_GUARD_LOG: os.path.join(c.w, GUARD_LOG_FILE),
    }


def safe_run_one(c, d) -> tuple[dict, dict, dict]:
    """run_one for the wave pool: an exception becomes a BLOCKED result and the report is still recorded, so one crashing dispatch does not abort the wave."""
    try:
        return run_one(c, d)
    except Exception as e:
        f = work_path(c.w, f"report-{d['id']}.md", create=True)
        u = dict(claude.usage({}), error=True, result=f"{VERDICT_PREFIX}BLOCKED\nwave: dispatch {d.get('id', '?')} crashed: {e!r}")
        if not read_text(f).strip():
            write_text(f, u["result"])
        return d, u, orch("report", c.stage, c.dir, "--id", d["id"])


def run_one(c, d) -> tuple[dict, dict, dict]:
    f = work_path(c.w, f"report-{d['id']}.md", create=True)
    reset_failed = False
    if d["agent"] == "script":
        u = run_script(d["prompt"], c.dir, timeout=script_step_limit(project(c.dir)))
    else:
        turns = _auditor_turns(c, d) if d["agent"] in AUDITORS else None
        dev_dirs = (
            (os.path.join(c.dir, WORK_DIR),) if d["agent"] in WORKTREE_CWD_AGENTS else ()
        )  # dev reads the stage reports and scratch under the project .work
        args = claude.claude_args(
            d["agent"], d["model"], d.get("effort"), d["prompt"], dir=c.dir, max_turns=turns, scratch=scratch_env(c)[ENV_SCRATCH_DIR], add_dirs=dev_dirs
        )
        if d["agent"] != REVIEWER:  # dev runs in its job worktree with the write guard env; the read-only roles keep the project dir
            u = claude.session(
                args,
                session_cwd(c, d),
                done_file=f,
                env_extra={**scratch_env(c), ENV_AGENT_KIND: d["agent"], **guard_env(c, d)},
                pgid_file=os.path.join(c.w, PGID_FILE_NAME),
            )
        else:  # mutation checks run in the job worktree; the script puts it back whatever the session does
            task = dispatch_task(c, d)
            why = review_begin(c, task)
            if why:
                u = dict(claude.usage({}), error=True, result=f"{VERDICT_PREFIX}BLOCKED\nreview not started: {why}")
            else:
                try:
                    u = claude.session(
                        args,
                        c.dir,
                        done_file=f,
                        env_extra={
                            **scratch_env(c),
                            ENV_AGENT_KIND: d["agent"],
                            ENV_REVIEW_DIR: review_root(c, task),
                            ENV_GATE_KIND: GATE_KIND_MUTATION,
                        },
                        pgid_file=os.path.join(c.w, PGID_FILE_NAME),
                    )
                finally:
                    bad = review_reset(c, task)
                if bad:
                    reset_failed = True
                    u = dict(u, error=True, result=f"{VERDICT_PREFIX}BLOCKED\nreview reset failed: {bad}")
    if u.get("stopped"):  # the driver is stopping: the session has no answer, so the report is not written
        return d, u, {}
    if not read_text(f).strip():
        write_text(f, u["result"])
    elif reset_failed:  # the reviewer already wrote a report: the failed reset still decides the verdict, its text stays below
        write_text(f, u["result"] + "\n\n" + read_text(f))
    if d["agent"] in AUDITORS:
        save_audit_reply(c.w, dispatch_task(c, d), u["result"])
    rep = orch("report", c.stage, c.dir, "--id", d["id"])
    return d, u, rep


def do_wave(c, ds) -> None:
    t0 = time.time()
    tasks = c.tasks()
    with claude.kill_on_signal(), ThreadPoolExecutor(max_workers=max(1, len(ds))) as ex:
        outs = list(ex.map(lambda d: safe_run_one(c, d), ds))
    for d, u, rep in outs:
        metrics.session_line(c, d["id"], d["agent"], d["model"], u, rep.get("verdict", ""), tasks.get(d["id"], ""), rep.get("why", ""))
    metrics.wave_line(c, time.time() - t0)
