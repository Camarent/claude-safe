"""Headless `claude -p` session: build args, run with a timeout, parse the usage into one flat dict."""

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
from datetime import datetime, timedelta
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
    ENV_PROJECT_ROOT,
    ENV_TIMEOUT,
    GIT_SAFE_ENV,
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
    TUNING_EXEMPT_AGENTS,
    ConfigError,
    ops_flags,
    project,
    scratch_allow,
)  # module-level names so tests can patch them

ENV_PIPELINE_TOKEN = "PIPELINE_CLAUDE_TOKEN"  # nosec B105 (the env var NAME, not a secret); long-lived `claude setup-token` token of a second account, for headless sessions only
NO_TOKEN_AGENTS = ("publisher", "pl-publisher")  # container name, host name; need the main login (artifacts): a setup-token token only makes model requests

SUMS = ("turns", "input", "output", "cache_read", "cache_write", "cost_usd", "seconds")


def flags(agent=None):
    args = shlex.split(os.environ.get(ENV_CLAUDE_FLAGS, DEFAULT_FLAGS))
    if agent in TUNING_EXEMPT_AGENTS:  # e.g. publisher needs Skill: no AGENT_TUNING_FLAGS for it
        args = [a for a in args if a not in AGENT_TUNING_FLAGS]
    return args


def default_timeout():
    try:
        return float(os.environ.get(ENV_TIMEOUT, SESSION_TIMEOUT_S))
    except ValueError:
        return SESSION_TIMEOUT_S


def project_allow(d=None):
    """Extra `--allowedTools` entries from <d>/pipeline.json `allow` (list of Bash patterns such as "Bash(target/release/app check *)"), for every headless session; [] when unset. d defaults to the cwd."""
    allow = project(d or os.getcwd()).get("allow")
    if not isinstance(allow, list) or not allow:
        return []
    return ["--allowedTools", *(str(a) for a in allow)]


def scratch_allow_args(scratch, d=None):
    """`--allowedTools` entries letting the agent run python scripts from SCRATCH; [] when none."""
    allow = scratch_allow(scratch, project(d or os.getcwd()))
    return ["--allowedTools", *(f"Bash({a})" for a in allow)] if allow else []


def claude_args(agent, model, effort, prompt, dir=None, max_turns=None, scratch=None):
    """Args for `claude -p`. dir: the run dir whose pipeline.json and .work/ apply (default: the cwd). max_turns: `--max-turns` when > 0. scratch: the stage scratch dir whose script files the agent may run (see scratch_allow)."""
    return [
        "-p",
        *(["--agent", agent] if agent else []),
        *(["--model", model] if model else []),
        *(["--effort", effort] if effort else []),
        *(["--max-turns", str(max_turns)] if max_turns and max_turns > 0 else []),
        "--output-format",
        "json",
        *flags(agent),
        *((ops_flags(dir) if dir else OPS_FLAGS) if agent == "ops" else []),
        *project_allow(dir),
        *scratch_allow_args(scratch, dir),
        "--",
        prompt,
    ]


def report_done(path):
    """True when the agent's report file exists and its first line is a VERDICT line."""
    try:
        with open(path) as f:
            return f.readline().startswith("VERDICT:")
    except OSError:
        return False


LIVE: list[subprocess.Popen] = []  # Popen objects (sessions and script steps) this process started and has not yet reaped
STOPPING = False  # set by kill_on_signal: no new session starts once the driver is told to stop


def track(p):
    LIVE.append(p)
    return p


def untrack(p):
    if p in LIVE:
        LIVE.remove(p)


def kill_group(p):
    with contextlib.suppress(OSError):
        os.killpg(p.pid, signal.SIGKILL)  # the session leads its own group, so background children die with it


def kill_all():
    """Kill the process group of every live session or script step (driver stop)."""
    for p in list(LIVE):
        kill_group(p)


@contextlib.contextmanager
def kill_on_signal():
    """Driver guard: SIGTERM and SIGINT kill every live session group, then exit. Without it the headless claude sessions outlive the driver and keep editing the job worktree."""
    global STOPPING
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum, frame):
        global STOPPING
        STOPPING = True
        kill_all()
        raise SystemExit(128 + signum)

    old = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
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


def artifact_allow(args):
    """args with ARTIFACT_ALLOW_FLAGS for the publisher only, placed before the `--` that ends the flags so the prompt stays the last positional; other sessions get args unchanged."""
    if not any(a in args for a in ARTIFACT_AGENTS):
        return list(args)
    cut = args.index("--") if "--" in args else max(len(args) - 1, 0)
    return [*args[:cut], *ARTIFACT_ALLOW_FLAGS, *args[cut:]]


def add_dirs():
    """Dirs for ADD_DIR_FLAG: pipeline.json `add_dirs` if a list, else the project root."""
    root = os.environ.get(ENV_PROJECT_ROOT) or DEFAULT_WORKSPACE
    try:
        extra = project(root).get(ADD_DIRS_KEY)
    except (ConfigError, OSError):
        extra = None
    return [str(d) for d in extra] if isinstance(extra, list) and extra else [root]


def add_dir_allow(args):
    """args with ADD_DIR_FLAG <dir> placed before the `--` that ends the flags: the project dirs for the read-only roles in ADD_DIR_AGENTS, only SCRATCH_ADD_DIR for dev and reviewer (SCRATCH_ADD_AGENTS)."""
    if any(a in args for a in ADD_DIR_AGENTS):
        dirs = add_dirs()
    elif any(a in args for a in SCRATCH_ADD_AGENTS):
        dirs = [SCRATCH_ADD_DIR]
    else:
        return list(args)
    cut = args.index("--") if "--" in args else max(len(args) - 1, 0)
    return [*args[:cut], *(x for d in dirs for x in (ADD_DIR_FLAG, d)), *args[cut:]]


def run_claude(args, cwd, timeout, done_file=None, env_extra=None):
    """Run `claude <args>`, -> parsed json dict (is_error set on any failure). done_file: report path; session is closed REPORT_GRACE_S after its VERDICT line appears."""
    if STOPPING:
        return {"is_error": True, "result": "wave: driver stopping, session not started"}
    p = None
    try:
        env = {**os.environ, **GIT_SAFE_ENV}
        token = os.environ.get(ENV_PIPELINE_TOKEN)
        if token and not any(a in args for a in NO_TOKEN_AGENTS):
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        if any(a in args for a in ARTIFACT_AGENTS):
            env[ENV_ARTIFACT] = ARTIFACT_ON
        env.update(env_extra or {})
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
        out, err, closed, timed_out = _wait(p, timeout, done_file)
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


def tool_counts(session_id):
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


def search_counts(session_id):
    """local.py vs grep/glob call counts of a session transcript (see lp_share.py); {} if absent."""
    import glob

    from . import lp_share

    if not session_id or not str(session_id).replace("-", "").isalnum():
        return {}
    c: dict = {}
    for f in glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{session_id}.jsonl")):
        c = lp_share.count_file(f, c) if c else lp_share.count_file(f)
    return dict(c)


def denial_lines(denials):
    """Raw `permission_denials` of a claude json -> lines `Tool: command-or-path` (max 5, DENIED_CMD_MAX chars each, plus ` [reason]` when the json gives one)."""
    out = []
    for d in denials or []:
        if not isinstance(d, dict):
            continue
        i = d.get("tool_input") or {}
        what = i.get("command") or i.get("file_path") or i.get("path") or ""
        line = f"{d.get('tool_name', '?')}: {str(what)[:DENIED_CMD_MAX]}"
        why = d.get("reason") or d.get("message")
        out.append(f"{line} [{str(why)[:200]}]" if why else line)
    return out[:5]


def usage(res):
    """Raw claude json -> {turns, input, output, cache_read, cache_write, cost_usd, seconds, seconds_api, result, error}."""
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
        "denials": denial_lines(res.get("permission_denials")),
        "sid": res.get("session_id") or "",
        "tools": tool_counts(res.get("session_id")),
        "search": search_counts(res.get("session_id")),
    }


LIMIT_RE = re.compile(r"hit your .{0,30}limit", re.I)
RESET_RE = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(?:\(([^)]+)\))?", re.I)


def limit_wait_s(res, now=None):
    """-> seconds to wait when RES is a usage-limit notice (`You've hit your 5-hour limit · resets 8pm (UTC)`), else None.
    Without a parsable reset time: LIMIT_FALLBACK_S. The notice is a short result; an agent's long text never counts."""
    text = str(res.get("result") or "").strip()
    if not text or len(text) > LIMIT_TEXT_MAX or not LIMIT_RE.search(text):
        return None
    m = RESET_RE.search(text)
    if not m:
        return LIMIT_FALLBACK_S
    try:
        tz = ZoneInfo(m[4]) if m[4] else None
    except (KeyError, ValueError, OSError):  # unknown zone name
        return LIMIT_FALLBACK_S
    t = datetime.fromtimestamp(now, tz) if now is not None else datetime.now(tz)
    hour = int(m[1]) % 12 + (12 if m[3].lower() == "pm" else 0)
    reset = t.replace(hour=hour, minute=int(m[2] or 0), second=0, microsecond=0)
    if reset <= t:
        reset += timedelta(days=1)
    return (reset - t).total_seconds() + LIMIT_MARGIN_S


def session(args, cwd, timeout=None, done_file=None, env_extra=None):
    """Run one session (timeout default WAVE_TIMEOUT; done_file: see run_claude). -> usage dict.
    A usage-limit notice as the result is not a failure of the task: wait for the printed reset time and run the session again (at most LIMIT_WAIT_MAX_S in all)."""
    t = default_timeout() if timeout is None else timeout
    waited = 0.0
    while True:
        if env_extra:
            res = run_claude(args, cwd, t, done_file, env_extra)
        elif done_file:
            res = run_claude(args, cwd, t, done_file)
        else:
            res = run_claude(args, cwd, t)
        wait = limit_wait_s(res)
        if wait is None or waited + wait > LIMIT_WAIT_MAX_S:
            return usage(res)
        print(f"wave: usage limit hit ({res.get('result')!r}); waiting {wait / 60:.0f} min, then running the session again", file=sys.stderr, flush=True)
        time.sleep(wait)
        waited += wait
