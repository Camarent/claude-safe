#!/usr/bin/env python3
"""servectl: start a dev server that ends itself after a TTL, list the servers started, stop them.

    python3 -I /root/.claude/tools/servectl.py start [--ttl S] [--port-wait PORT] [--state-dir D] -- <command...>
    python3 -I /root/.claude/tools/servectl.py list [--state-dir D]
    python3 -I /root/.claude/tools/servectl.py stop [--state-dir D]

`start` runs the command detached in its own process group under a supervisor that kills the group after the TTL. It prints
`pid N`, `log PATH`, `state PATH` and, with --port-wait, `ready PORT` (or it stops the server and exits 1 when the port never opens).
`start` refuses (exit 2) any command not allowed by the cwd's pipeline.json `serve_commands` plus the default loopback static server
(pipeline/serve_rule.py); no shell runs the command. `stop` sends SIGTERM to every recorded group, SIGKILL after a grace period, and removes the state files; it is idempotent and
prints `VERDICT: DONE` (or `VERDICT: FAIL` when a group survives SIGKILL). Agents never need kill or pkill.
State dir: --state-dir, else env SERVECTL_DIR, else `<cwd>/.work/servectl`."""

import argparse
import contextlib
import json
import math
import os
import secrets
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline.configuration import (
    SERVE_DIR_ENV,
    SERVE_DIR_NAME,
    SERVE_HOST,
    SERVE_POLL_S,
    SERVE_PORT_WAIT_S,
    SERVE_STATE_SUFFIX,
    SERVE_STOP_GRACE_S,
    SERVE_TTL_ENV,
    SERVE_TTL_S,
    VERDICT_PREFIX,
    WORK_DIR,
)
from pipeline.serve_rule import start_refusal

PROC = "/proc"  # Linux process table: group membership, zombie state and start time
STARTTIME_INDEX = 19  # index after `(comm)` in /proc/<pid>/stat: state is field 3, so starttime (field 22) is index 19
ZOMBIE = "Z"
SUPERVISE = "_supervise"  # hidden subcommand: the watcher process that runs the server and enforces the TTL


def state_dir(flag):
    """-> the state dir: --state-dir, else env SERVECTL_DIR, else <cwd>/WORK_DIR/SERVE_DIR_NAME."""
    return os.path.abspath(flag or os.environ.get(SERVE_DIR_ENV) or os.path.join(WORK_DIR, SERVE_DIR_NAME))


def ttl_default():
    """-> the default TTL in seconds: env SERVECTL_TTL, else SERVE_TTL_S. A bad value gives SERVE_TTL_S."""
    try:
        return float(os.environ.get(SERVE_TTL_ENV, SERVE_TTL_S))
    except ValueError:
        return SERVE_TTL_S


def check_ttl(value):
    """-> the TTL as a positive finite number of seconds, else raise argparse's error."""
    try:
        v = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from e
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError(f"TTL must be a positive finite number of seconds: {value}")
    return v


def proc_stat(pid):
    """-> (state, pgrp, starttime) from /proc/<pid>/stat, or None when the process is gone or /proc is missing."""
    try:
        with open(os.path.join(PROC, str(pid), "stat"), encoding="utf-8") as f:
            rest = f.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return rest[0], int(rest[2]), int(rest[STARTTIME_INDEX])


def group_live(pgid):
    """True while a process of group PGID exists and is not a zombie. Without /proc, falls back to killpg(0)."""
    if not os.path.isdir(PROC):
        try:
            os.killpg(pgid, 0)
            return True
        except OSError:
            return False
    for name in os.listdir(PROC):
        if not name.isdigit():
            continue
        st = proc_stat(int(name))
        if st and st[1] == pgid and st[0] != ZOMBIE:
            return True
    return False


def same_leader(pid, starttime):
    """False only when PID now belongs to a different process (start time differs): then the group id may be reused. A gone leader
    still owns its group while members live (Linux does not reuse a pgid in use), so that is True."""
    st = proc_stat(pid)
    return st is None or starttime is None or st[2] == starttime


def wait_gone(pgid, grace):
    """Poll until group PGID has no live member or `grace` seconds pass. -> True when the group is gone."""
    end = time.monotonic() + grace
    while group_live(pgid):
        if time.monotonic() >= end:
            return False
        time.sleep(SERVE_POLL_S)
    return True


def terminate(rec):
    """Stop the group of record REC: SIGTERM, then SIGKILL after the grace period. -> True when the group is gone."""
    pgid = rec["pgid"]
    if not group_live(pgid):
        return True
    if not same_leader(rec["pid"], rec.get("starttime")):
        return not group_live(pgid)  # the pid was reused: never signal a group we did not start
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(OSError):
            os.killpg(pgid, sig)
        if wait_gone(pgid, SERVE_STOP_GRACE_S):
            return True
    return False


def load_records(d):
    """-> [(path, record)] of the state files in D (empty when D does not exist). Unreadable files are skipped."""
    out = []
    if not os.path.isdir(d):
        return out
    for n in sorted(os.listdir(d)):
        if not n.endswith(SERVE_STATE_SUFFIX):
            continue
        p = os.path.join(d, n)
        try:
            with open(p, encoding="utf-8") as f:
                out.append((p, json.load(f)))
        except (OSError, ValueError):
            continue
    return out


def port_open(port):
    """True when something accepts TCP connections on SERVE_HOST:PORT."""
    try:
        with socket.create_connection((SERVE_HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_port(port, bound, proc):
    """Poll until PORT accepts or BOUND seconds pass or the server process ends. -> True when the port opened."""
    end = time.monotonic() + bound
    while time.monotonic() < end:
        if port_open(port):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(SERVE_POLL_S)
    return False


def split_command(argv):
    """-> (options, command) split at the first `--`; a missing `--` or an empty command is an error."""
    if "--" not in argv:
        raise SystemExit("servectl start: the server command must follow `--`")
    i = argv.index("--")
    return argv[:i], argv[i + 1 :]


def cmd_start(opts, command):
    """Start COMMAND under a supervisor with TTL, record it, optionally wait for PORT. -> exit code."""
    d = state_dir(opts.state_dir)
    os.makedirs(d, exist_ok=True)
    sid = secrets.token_hex(4)
    log_path = os.path.join(d, f"{sid}.log")
    state_path = os.path.join(d, f"{sid}{SERVE_STATE_SUFFIX}")
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), SUPERVISE, "--ttl", repr(opts.ttl), "--", *command],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=os.getcwd(),
        )
    rec = {
        "id": sid,
        "pid": proc.pid,
        "pgid": proc.pid,
        "starttime": (proc_stat(proc.pid) or (None, None, None))[2],
        "ttl": opts.ttl,
        "started": time.time(),
        "port": opts.port_wait,
        "command": command,
        "log": log_path,
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    print(f"pid {proc.pid}")
    print(f"log {log_path}")
    print(f"state {state_path}")
    if opts.port_wait is None:
        return 0
    if wait_port(opts.port_wait, opts.wait_timeout, proc):
        print(f"ready {opts.port_wait}")
        return 0
    terminate(rec)
    with contextlib.suppress(OSError):
        os.remove(state_path)
    print(f"ready-fail port {opts.port_wait} did not open within {opts.wait_timeout:g}s; server stopped, see {log_path}")
    return 1


def cmd_list(opts):
    """Print one line per recorded server: id, pid, alive/gone, seconds left of the TTL, log, command."""
    now = time.time()
    for _, rec in load_records(state_dir(opts.state_dir)):
        alive = group_live(rec["pgid"])
        left = max(0.0, rec["ttl"] - (now - rec["started"]))
        print(
            f"{rec['id']} pid {rec['pid']} {'alive' if alive else 'gone'} ttl {rec['ttl']:g}s left {left:.0f}s log {rec['log']} cmd {' '.join(rec['command'])}"
        )
    return 0


def cmd_stop(opts):
    """Stop every recorded server, remove its state file. Idempotent: no records prints nothing and exits 0. -> exit code."""
    lines, bad = [], False
    for path, rec in load_records(state_dir(opts.state_dir)):
        ok = terminate(rec)
        bad = bad or not ok
        lines.append(f"{rec['id']} pid {rec['pid']} {'stopped' if ok else 'STILL RUNNING'}")
        if ok:
            with contextlib.suppress(OSError):
                os.remove(path)
    print(VERDICT_PREFIX + ("FAIL" if bad else "DONE"))
    print("\n".join(lines))
    return 1 if bad else 0


def cmd_supervise(ttl, command):
    """Run COMMAND in this group (stdout/stderr are the log); after TTL seconds stop the whole group. -> exit code."""
    signal.signal(signal.SIGTERM, lambda *_: None)  # the group SIGTERM from stop/TTL reaches us too: the child dies, we exit with it
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    try:
        return child.wait(timeout=ttl)
    except subprocess.TimeoutExpired:
        print(f"servectl: TTL {ttl:g}s reached, stopping the server group", flush=True)
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(0), signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=SERVE_STOP_GRACE_S)
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(0), signal.SIGKILL)
        return 0


def build_parser():
    ap = argparse.ArgumentParser(description="start, list and stop TTL-bound dev servers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "list", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--state-dir", default=None, help=f"state dir (default env {SERVE_DIR_ENV}, else <cwd>/{WORK_DIR}/{SERVE_DIR_NAME})")
        if name == "start":
            p.add_argument(
                "--ttl", type=check_ttl, default=ttl_default(), help=f"seconds until the server ends itself (default {SERVE_TTL_S:g}, env {SERVE_TTL_ENV})"
            )
            p.add_argument("--port-wait", type=int, default=None, metavar="PORT", help="wait until 127.0.0.1:PORT accepts connections")
            p.add_argument("--wait-timeout", type=check_ttl, default=SERVE_PORT_WAIT_S, help=f"bound of --port-wait in seconds (default {SERVE_PORT_WAIT_S:g})")
    return ap


def main(argv):
    if argv[:1] == [SUPERVISE]:  # hidden: the watcher, spawned by `start`
        opts, command = split_command(argv[1:])
        ttl = check_ttl(opts[opts.index("--ttl") + 1]) if "--ttl" in opts else SERVE_TTL_S
        return cmd_supervise(ttl, command)
    if "--" in argv:
        opts, command = split_command(argv)
    else:
        opts, command = argv, []
    args = build_parser().parse_args(opts)
    if args.cmd == "start":
        if not command:
            raise SystemExit("servectl start: give the server command after `--`")
        why = start_refusal(command, os.getcwd())  # the enforcement: only the allowed server commands start, no shell
        if why:
            print(f"servectl start refused: {why}", file=sys.stderr)
            return 2
        return cmd_start(args, command)
    if command:
        raise SystemExit(f"servectl {args.cmd}: takes no command")
    return cmd_list(args) if args.cmd == "list" else cmd_stop(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
