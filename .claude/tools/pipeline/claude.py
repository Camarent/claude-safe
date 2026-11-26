"""Headless `claude -p` session: build args, run with a timeout, parse the usage into one flat dict."""

import calendar
import contextlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any, NamedTuple, NoReturn
from zoneinfo import ZoneInfo

from .configuration import (
    ADD_DIR_AGENTS,
    ADD_DIR_FLAG,
    ADD_DIRS_KEY,
    AGENT_TUNING_FLAGS,
    ARTIFACT_AGENTS,
    ARTIFACT_ALLOW_FLAGS,
    ARTIFACT_ON,
    DEFAULT_FLAGS,
    DEFAULT_WORKSPACE,
    DENIED_CMD_MAX,
    ENV_ARTIFACT,
    ENV_CLAUDE_FLAGS,
    ENV_DENIAL_SESSION,
    ENV_PROJECT_ROOT,
    ENV_SCRATCH_DIR,
    ENV_TIMEOUT,
    GIT_SAFE_ENV,
    INIT_PID,
    LIMIT_FALLBACK_S,
    LIMIT_MARGIN_S,
    LIMIT_TEXT_MAX,
    LIMIT_WAIT_MAX_S,
    OPS_FLAGS,
    POLL_S,
    REPORT_GRACE_S,
    SCRATCH_ADD_AGENTS,
    SCRATCH_ADD_DIR,
    SESSION_TIMEOUT_S,
    STOP_SIGNALS,
    TUNING_EXEMPT_AGENTS,
    ConfigError,
    ops_flags,
    project,
    scratch_allow,
    tool_setting,
)  # module-level names so tests can patch them
from .util import write_text

ENV_PIPELINE_TOKEN = "PIPELINE_CLAUDE_TOKEN"  # nosec B105 (the env var NAME, not a secret); long-lived `claude setup-token` token of a second account, for headless sessions only
NO_TOKEN_AGENTS = ("publisher", "pl-publisher")  # container name, host name; need the main login (artifacts): a setup-token token only makes model requests

SUMS = ("turns", "input", "output", "cache_read", "cache_write", "cost_usd", "seconds")


def flags(agent=None) -> list[str]:
    args = shlex.split(os.environ.get(ENV_CLAUDE_FLAGS, DEFAULT_FLAGS))
    if agent in TUNING_EXEMPT_AGENTS:  # e.g. publisher needs Skill: no AGENT_TUNING_FLAGS for it
        args = [a for a in args if a not in AGENT_TUNING_FLAGS]
    return args


def default_timeout() -> float:
    try:
        return float(os.environ.get(ENV_TIMEOUT, SESSION_TIMEOUT_S))
    except ValueError:
        return SESSION_TIMEOUT_S


def project_allow(d=None) -> list[str]:
    """Extra `--allowedTools` entries from <d>/pipeline.json `allow` (list of Bash patterns such as "Bash(target/release/app check *)"), for every headless session; [] when unset. d defaults to the cwd."""
    allow = project(d or os.getcwd()).get("allow")
    if not isinstance(allow, list) or not allow:
        return []
    return ["--allowedTools", *(str(a) for a in allow)]


def scratch_allow_args(scratch, d=None) -> list[str]:
    """`--allowedTools` entries letting the agent run python scripts from SCRATCH; [] when none."""
    allow = scratch_allow(scratch, project(d or os.getcwd()))
    return ["--allowedTools", *(f"Bash({a})" for a in allow)] if allow else []


def claude_args(agent, model, effort, prompt, dir=None, max_turns=None, scratch=None, add_dirs=()) -> list[str]:
    """Args for `claude -p`. dir: the run dir whose pipeline.json and .work/ apply (default: the cwd). max_turns: `--max-turns` when > 0. scratch: the stage scratch dir whose script files the agent may run (see scratch_allow). add_dirs: extra dirs the session may read, each as ADD_DIR_FLAG."""
    return [
        "-p",
        *(["--agent", agent] if agent else []),
        *(["--model", model] if model else []),
        *(["--effort", effort] if effort else []),
        *(["--max-turns", str(max_turns)] if max_turns and max_turns > 0 else []),
        "--output-format",
        "json",
        *flags(agent),
        *(x for d in add_dirs for x in (ADD_DIR_FLAG, d)),
        *((ops_flags(dir) if dir else OPS_FLAGS) if agent == "ops" else []),
        *project_allow(dir),
        *scratch_allow_args(scratch, dir),
        "--",
        prompt,
    ]


def report_done(path) -> bool:
    """True when the agent's report file exists and its first line is a VERDICT line."""
    try:
        with open(path) as f:
            return f.readline().startswith("VERDICT:")
    except OSError:
        return False


LIVE: list[subprocess.Popen] = []  # Popen objects (sessions and script steps) this process started and has not yet reaped
STOPPING = False  # set by kill_on_signal: no new session starts once the driver is told to stop


def track(p) -> subprocess.Popen:
    LIVE.append(p)
    return p


def untrack(p) -> None:
    if p in LIVE:
        LIVE.remove(p)


PGID_LOCK = threading.Lock()  # sessions of one wave start and end on pool threads


def pgid_record(path, pid) -> None:
    """Append PID (a session's process group id: it leads its own group) as one line of the stage's pgid file PATH."""
    with PGID_LOCK, open(path, "a") as f:
        f.write(f"{pid}\n")


def pgid_forget(path, pid) -> None:
    """Drop PID's line from the pgid file PATH; the file goes when no live session is left in it."""
    with PGID_LOCK:
        try:
            with open(path) as f:
                left = [line for line in f if line.strip() != str(pid)]
        except OSError:
            return
        if left:
            write_text(path, "".join(left))
        else:
            os.remove(path)


def kill_group(p) -> None:
    with contextlib.suppress(OSError):
        os.killpg(p.pid, signal.SIGKILL)  # the session leads its own group, so background children die with it


def kill_all() -> None:
    """Kill the process group of every live session or script step (driver stop)."""
    for p in list(LIVE):
        kill_group(p)


@contextlib.contextmanager
def kill_on_signal() -> Iterator[None]:
    """Driver guard: SIGTERM and SIGINT kill every live session group, then exit. Without it the headless claude sessions outlive the driver and keep editing the job worktree."""
    global STOPPING
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum, frame) -> NoReturn:
        global STOPPING
        STOPPING = True
        kill_all()
        raise SystemExit(128 + signum)

    sigs = [getattr(signal, name) for name in STOP_SIGNALS]
    if os.getpid() == INIT_PID:  # container init: a hangup is not a stop request, leave SIGHUP alone
        sigs = [s for s in sigs if s != signal.SIGHUP]
    old = {sig: signal.signal(sig, stop) for sig in sigs}
    try:
        yield
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        STOPPING = False


def _wait(p, timeout, done_file):
    """Wait for session P -> (out, err, closed, timed_out). Killed at TIMEOUT seconds; closed once its VERDICT line is REPORT_GRACE_S old."""
    t0, seen = time.time(), None
    while True:
        try:
            out, err = p.communicate(timeout=POLL_S)
            return out, err, False, False
        except subprocess.TimeoutExpired:
            now = time.time()
            if now - t0 > timeout:
                kill_group(p)
                p.communicate()
                return None, None, False, True
            if done_file and report_done(done_file):
                seen = seen or now
                if now - seen > REPORT_GRACE_S:
                    kill_group(p)
                    out, err = p.communicate()
                    return out, err, True, False


def artifact_allow(args) -> list[str]:
    """args with ARTIFACT_ALLOW_FLAGS for the publisher only, placed before the `--` that ends the flags so the prompt stays the last positional; other sessions get args unchanged."""
    if not any(a in args for a in ARTIFACT_AGENTS):
        return list(args)
    cut = args.index("--") if "--" in args else max(len(args) - 1, 0)
    return [*args[:cut], *ARTIFACT_ALLOW_FLAGS, *args[cut:]]


def add_dirs() -> list[str]:
    """Dirs for ADD_DIR_FLAG: pipeline.json `add_dirs` if a list, else the project root."""
    root = os.environ.get(ENV_PROJECT_ROOT) or DEFAULT_WORKSPACE
    try:
        extra = project(root).get(ADD_DIRS_KEY)
    except (ConfigError, OSError):
        extra = None
    return [str(d) for d in extra] if isinstance(extra, list) and extra else [root]


def add_dir_allow(args) -> list[str]:
    """args with ADD_DIR_FLAG <dir> placed before the `--` that ends the flags: the project dirs for the read-only roles in ADD_DIR_AGENTS, only SCRATCH_ADD_DIR for dev and reviewer (SCRATCH_ADD_AGENTS)."""
    if any(a in args for a in ADD_DIR_AGENTS):
        dirs = add_dirs()
    elif any(a in args for a in SCRATCH_ADD_AGENTS):
        dirs = [SCRATCH_ADD_DIR]
    else:
        return list(args)
    cut = args.index("--") if "--" in args else max(len(args) - 1, 0)
    return [*args[:cut], *(x for d in dirs for x in (ADD_DIR_FLAG, d)), *args[cut:]]


def session_env(args, env_extra) -> dict[str, str]:
    """Environment of one `claude` session: GIT_SAFE_ENV, the pipeline token unless the agent is in NO_TOKEN_AGENTS, the artifact flag for ARTIFACT_AGENTS, then env_extra."""
    env = {**os.environ, **GIT_SAFE_ENV}
    token = os.environ.get(ENV_PIPELINE_TOKEN)
    if token and not any(a in args for a in NO_TOKEN_AGENTS):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    if any(a in args for a in ARTIFACT_AGENTS):
        env[ENV_ARTIFACT] = ARTIFACT_ON
    env.update(env_extra or {})
    return env


def run_claude(args, cwd, timeout, done_file=None, env_extra=None, pgid_file=None) -> dict[str, Any]:
    """Run `claude <args>`, -> parsed json dict (is_error set on any failure). done_file: report path; session is closed REPORT_GRACE_S after its VERDICT line appears. pgid_file: stage file that lists the live sessions' group ids. stopped=True when the driver is stopping: the session did not run to an answer, so no report is due."""
    if STOPPING:
        return {"stopped": True, "is_error": True, "result": "wave: driver stopping, session not started"}
    p = None
    try:
        env = session_env(args, env_extra)
        p = track(
            subprocess.Popen(
                ["claude", *add_dir_allow(artifact_allow(args))],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        )
        if pgid_file:
            try:
                pgid_record(pgid_file, p.pid)
            except OSError:
                kill_group(p)  # unrecorded session would outlive the driver: kill it, then report the failure
                raise
        out, err, closed, timed_out = _wait(p, timeout, done_file)
        if STOPPING:  # killed by the stop signal: no answer, no report
            return {"stopped": True, "is_error": True, "result": "wave: driver stopped, session killed"}
        if timed_out:
            return {"is_error": True, "result": "wave: session timeout"}
        try:
            return json.loads(out)
        except ValueError:
            if closed:
                return {"is_error": False, "result": "wave: report written, hung session closed"}
            return {"is_error": True, "result": f"wave: claude rc={p.returncode}: {(err or out)[:300]}"}
    except Exception as e:
        return {"is_error": True, "result": f"wave: claude call failed: {e}"}
    finally:
        if p is not None:
            untrack(p)
            if pgid_file:
                pgid_forget(pgid_file, p.pid)


def tool_counts(session_id) -> dict[str, int]:
    """Per-tool call counts from the persisted session transcript (~/.claude/projects/*/<id>.jsonl); {} if absent."""
    import collections
    import glob

    if not session_id or not str(session_id).replace("-", "").isalnum():
        return {}
    c: collections.Counter[str] = collections.Counter()
    for f in glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{session_id}.jsonl")):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        m = (json.loads(line).get("message") or {}).get("content")
                    except ValueError:
                        continue
                    for b in m if isinstance(m, list) else []:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name"):
                            c[b["name"]] += 1
        except OSError:
            pass
    return dict(c)


def search_counts(session_id) -> dict[str, int]:
    """local.py vs grep/glob call counts of a session transcript (see lp_share.py); {} if absent."""
    import glob

    from . import lp_share

    if not session_id or not str(session_id).replace("-", "").isalnum():
        return {}
    c: dict = {}
    for f in glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{session_id}.jsonl")):
        c = lp_share.count_file(f, c) if c else lp_share.count_file(f)
    return dict(c)


def guard_rows(scratch, token) -> list[tuple[str, str]]:
    """(command, rule) of each denials.jsonl row in the stage scratch dir that carries this session's `token`, in file order (denial_log.py); [] without a scratch dir."""
    if not scratch:
        return []
    import denial_log

    return [(r.get("command", ""), r.get("rule", "")) for r in denial_log.rows(scratch) if r.get("session") == token]


def denial_lines(denials, guards=()) -> list[str]:
    """Raw `permission_denials` of a claude json -> lines `Tool: command-or-path` (max 5, DENIED_CMD_MAX chars each, plus ` [reason]` when the json gives one, and the guard name last when a `guards` row (command, rule) matches the command; each row is used once)."""
    out = []
    left = list(guards)
    for d in denials or []:
        if not isinstance(d, dict):
            continue
        i = d.get("tool_input") or {}
        what = str(i.get("command") or i.get("file_path") or i.get("path") or "")
        line = f"{d.get('tool_name', '?')}: {what[:DENIED_CMD_MAX]}"
        why = d.get("reason") or d.get("message")
        if why:
            line = f"{line} [{str(why)[:200]}]"
        rule = _take_guard(left, what)
        out.append(f"{line} {rule}" if rule else line)
    return out[:5]


def _take_guard(left, what):
    """The rule of the first row in `left` whose command is `what` as denial_log.py cuts it (COMMAND_CHARS); that row is removed. "" when none; a row with an empty command matches nothing."""
    import denial_log

    for k, (cmd, rule) in enumerate(left):
        if cmd and what[: denial_log.COMMAND_CHARS] == cmd:
            del left[k]
            return rule
    return ""


def usage(res, guards=()) -> dict[str, Any]:
    """Raw claude json -> {turns, input, output, cache_read, cache_write, cost_usd, seconds, seconds_api, result, error}; guards: see denial_lines."""
    u = res.get("usage") or {}
    return {
        "turns": res.get("num_turns", 0),
        "input": u.get("input_tokens", 0),
        "output": u.get("output_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cost_usd": res.get("total_cost_usd", 0) or 0,
        "seconds": round((res.get("duration_ms", 0) or 0) / 1000, 1),
        "seconds_api": round((res.get("duration_api_ms", 0) or 0) / 1000, 1),
        "result": res.get("result") or "",
        "error": bool(res.get("is_error")),
        "stopped": bool(res.get("stopped")),
        "denials": denial_lines(res.get("permission_denials"), guards),
        "sid": res.get("session_id") or "",
        "tools": tool_counts(res.get("session_id")),
        "search": search_counts(res.get("session_id")),
    }


LIMIT_RE = re.compile(r"hit your .{0,30}limit", re.I)
# the reset forms seen in notices: `resets in 2 hours 10 minutes`, `resets 8pm (UTC)`, `resets 20:00 (UTC)`, `resets Nov 27, 8pm (UTC)`, `resets Nov 27 at 8:00pm`
RESET_IN_RE = re.compile(r"resets\s+in\s+(?:(?P<h>\d+)\s*h(?:ours?|rs?)?\s*)?(?:(?P<m>\d+)\s*min(?:ute)?s?)?", re.I)
RESET_AT_RE = re.compile(
    r"resets\s+(?:(?P<mon>[a-z]{3,9})\s+(?P<day>\d{1,2})(?:,|\s+at)?\s+)?(?P<h>\d{1,2})(?::(?P<min>\d{2}))?(?:\s*(?P<ampm>[ap]m))?\s*(?:\((?P<zone>[^)]+)\))?",
    re.I,
)
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}


class LimitWait(NamedTuple):
    seconds: float  # the wait, margin included (LIMIT_FALLBACK_S when no reset time is known)
    known: bool  # False: no parsable reset time or zone; the wait is a fallback, re-checked in slices


def _reset_time(m, t) -> datetime:
    """-> the reset time of the absolute RESET_AT_RE match M, counted from the aware time T. Raises ValueError, KeyError on an impossible value."""
    hour = int(m["h"]) % 12 + (12 if (m["ampm"] or "").lower() == "pm" else 0) if m["ampm"] else int(m["h"])
    reset = t.replace(hour=hour, minute=int(m["min"] or 0), second=0, microsecond=0)
    if m["mon"]:  # a month and day: a date already passed this year means next year
        reset = reset.replace(month=MONTHS[m["mon"][:3].lower()], day=int(m["day"]))
        return reset if reset > t else reset.replace(year=t.year + 1)
    return reset if reset > t else reset + timedelta(days=1)  # a clock time already passed today means tomorrow


def _relative_s(text) -> int | None:
    """-> the seconds of a `resets in 2 hours 10 minutes` form in TEXT, else None."""
    rel = RESET_IN_RE.search(text)
    if not rel or not (rel["h"] or rel["m"]):
        return None
    return int(rel["h"] or 0) * 3600 + int(rel["m"] or 0) * 60


def _absolute_wait(text, now_ts) -> LimitWait:
    """-> the wait to a clock or dated reset in TEXT, counted from NOW_TS; unknown (fallback) when it cannot be read."""
    m = RESET_AT_RE.search(text)
    if not m or (m["ampm"] is None and m["min"] is None):
        return LimitWait(LIMIT_FALLBACK_S, False)
    try:
        tz = ZoneInfo(m["zone"]) if m["zone"] else None
        t = datetime.fromtimestamp(now_ts, tz)
        reset = _reset_time(m, t)
    except (KeyError, ValueError, OSError):  # unknown zone name, no such month, impossible date or hour
        return LimitWait(LIMIT_FALLBACK_S, False)
    return LimitWait((reset - t).total_seconds() + LIMIT_MARGIN_S, True)


def limit_wait(res, now=None) -> LimitWait | None:
    """-> the wait when RES is a usage-limit notice (`You've hit your 5-hour limit · resets 8pm (UTC)`), else None.
    NOW: epoch seconds (default: the clock). The notice is a short result; an agent's long text never counts."""
    text = str(res.get("result") or "").strip()
    if not text or len(text) > LIMIT_TEXT_MAX or not LIMIT_RE.search(text):
        return None
    secs = _relative_s(text)
    if secs is not None:
        return LimitWait(secs + LIMIT_MARGIN_S, True)
    return _absolute_wait(text, time.time() if now is None else now)


def limit_wait_s(res, now=None) -> float | None:
    """-> seconds to wait when RES is a usage-limit notice, else None (see limit_wait)."""
    w = limit_wait(res, now)
    return None if w is None else w.seconds


def _limit_sleep(lw, sleep, now, recheck) -> float:
    """Sleep out the limit wait LW in slices of at most RECHECK seconds; -> seconds slept.
    Known reset: slices until NOW() reaches the reset time. Unknown: one fallback slice, then the session runs again."""
    if not lw.known:
        s = min(lw.seconds, recheck)
        print(f"wave: usage limit reset unknown; re-checking in {s / 60:.0f} min", file=sys.stderr, flush=True)
        sleep(s)
        return s
    reset = now() + lw.seconds
    slept = 0.0
    while (left := reset - now()) > 0:
        s = min(left, recheck)
        print(f"wave: usage limit wait, slice {s / 60:.0f} min of {left / 60:.0f} min left", file=sys.stderr, flush=True)
        sleep(s)
        slept += s
    return slept


def session(args, cwd, timeout=None, done_file=None, env_extra=None, pgid_file=None, sleep=time.sleep, now=time.time) -> dict[str, Any]:
    """Run one session (timeout default WAVE_TIMEOUT; done_file, pgid_file: see run_claude). -> usage dict.
    A usage-limit notice as the result is not a failure of the task: wait for the printed reset time in LIMIT_RECHECK_S slices and run the session again (at most LIMIT_WAIT_MAX_S in all).
    SLEEP and NOW are the clock of the wait (tests inject fakes)."""
    t = default_timeout() if timeout is None else timeout
    waited = 0.0
    token = uuid.uuid4().hex  # the scratch log is shared by the stage and its sessions run in parallel: denial_log.py stamps each row with the token, so only this session's rows name its denials
    env_extra = {**(env_extra or {}), ENV_DENIAL_SESSION: token}
    scratch = env_extra.get(ENV_SCRATCH_DIR, "")
    while True:
        extra = {k: v for k, v in (("done_file", done_file), ("env_extra", env_extra), ("pgid_file", pgid_file)) if v}
        res = run_claude(args, cwd, t, **extra)
        if res.get("stopped"):
            return usage(res, guard_rows(scratch, token))
        lw = limit_wait(res, now())
        if lw is None or waited + lw.seconds > LIMIT_WAIT_MAX_S:
            return usage(res, guard_rows(scratch, token))
        print(f"wave: usage limit hit ({res.get('result')!r}); waiting {lw.seconds / 60:.0f} min, then running the session again", file=sys.stderr, flush=True)
        waited += _limit_sleep(lw, sleep, now, tool_setting("LIMIT_RECHECK_S"))
