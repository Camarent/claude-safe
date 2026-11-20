#!/usr/bin/env python3
"""qrun: run a command, log full output, print a tiny summary or failure excerpts."""

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
import time

from pipeline.configuration import QRUN_ENV, QRUN_LOG_MAX_AGE_S, QRUN_LOG_MAX_COUNT, QRUN_LOG_MIN_KEEP, QRUN_TMP_PREFIX
from pipeline.util import script_timeout

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
NODE_KEY = re.compile(r"^(?:ℹ|#)\s+(tests|suites|pass|fail|cancelled|skipped|todo)\s+(\d+)\s*$")
CARGO_RES = re.compile(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored")
PYTEST = re.compile(r"^(?:=+ )?(\d+ (?:passed|failed|skipped|errors?)\b.*) in [\d.]+s")  # `=== ... ===` (default) or bare `-q` line
UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in [\d.]+s")
UNITTEST_END = re.compile(r"^(?:OK|FAILED)\b")
UNITTEST_SKIP = re.compile(r"\bskipped=(\d+)")
PR_SET_CHILD_SUBREAPER = 36  # prctl option (Linux): orphaned descendants reparent to qrun, so the /proc walk still finds them
KILL_ROUNDS = 5  # snapshot-and-kill passes: a process forked between snapshot and kill dies in the next
REAP_S = 5  # seconds to wait for the pipe to close after the kill
ENV_FAIL_ON_SKIP = "CI"  # set to 1: skipped unittest tests make a green run exit 3 (as ptest.py)
FAILWORD = re.compile(r"FAIL|ERROR|not ok|Error:")


def _node_summary(lines):
    node = {}
    for line in lines:
        m = NODE_KEY.match(line.strip())
        if m:
            node[m.group(1)] = int(m.group(2))
    if "tests" in node and "pass" in node:
        t = node["tests"]
        sk = node.get("skipped", 0)
        txt = f"node: {node['pass']} pass, {node.get('fail', 0)} fail, {node.get('todo', 0)} todo, {sk} skipped"
        return txt, t, sk
    return None


def _cargo_summary(lines):
    res = [CARGO_RES.match(line) for line in lines]
    hits = [m for m in res if m]
    if not hits:
        return None
    p = sum(int(m.group(2)) for m in hits)
    f = sum(int(m.group(3)) for m in hits)
    i = sum(int(m.group(4)) for m in hits)
    return f"cargo: {p} passed, {f} failed, {i} ignored", p + f + i, i


def _pytest_summary(lines):
    for line in reversed(lines):
        m = PYTEST.search(line)
        if m:
            s = m.group(1)
            n = sum(int(x) for x in re.findall(r"(\d+) (?:passed|failed|error)", s))
            sk = sum(int(x) for x in re.findall(r"(\d+) skipped", s))
            return "pytest: " + s, n + sk, sk
    return None


def _unittest_summary(lines):
    ran = [int(m.group(1)) for m in (UNITTEST_RAN.match(line) for line in lines) if m]
    ends = [line for line in lines if UNITTEST_END.match(line)]
    if not ran or not ends:
        return None
    m = UNITTEST_SKIP.search(ends[-1])
    sk = int(m.group(1)) if m else 0
    strict = os.environ.get(ENV_FAIL_ON_SKIP) == "1"  # `skipUnless(tool installed)` is normal outside CI
    return f"unittest: {ran[-1]} run, {sk} skipped", ran[-1], sk if strict else 0


def summary(lines):
    """Return (text, total, skipped) or None."""
    return _node_summary(lines) or _cargo_summary(lines) or _pytest_summary(lines) or _unittest_summary(lines)


def _node_failing_blocks(lines, start):
    """Split the 'failing tests:' section after line START into one block of lines per failed test."""
    blocks, cur = [], None
    for line in lines[start + 1 :]:
        if re.match(r"^(?:ℹ|#)\s", line):
            break
        if line.startswith("test at ") or (cur is None and line.strip()):
            cur = [line.rstrip()]
            blocks.append(cur)
        elif cur is not None and line.strip():
            cur.append(line.rstrip())
    return blocks


def _node_block_keep(block):
    """Keep a failure block's text and its first user stack frame (no node: internals); at most 8 lines."""
    keep, frame = [], False
    for line in block:
        s = line.strip()
        if s.startswith("at "):
            if not frame and "node:" not in s:
                keep.append(line)
                frame = True
            continue
        keep.append(line)
    return keep[:8]


def _node_tap_excerpts(lines):
    """TAP output (no 'failing tests:' section): each 'not ok' line plus its indented detail."""
    out = []
    i = 0
    while i < len(lines):
        if re.match(r"^\s*not ok\b", lines[i]):
            blk = [lines[i].rstrip()]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("#")) and not re.match(r"^\s*(not )?ok\b", lines[j]) and len(blk) < 8:
                if lines[j].strip() and "node:" not in lines[j]:
                    blk.append(lines[j].rstrip())
                j += 1
            out.extend(blk[:8])
            i = j
        else:
            i += 1
    return out


def node_excerpts(lines):
    start = next((i for i, line in enumerate(lines) if line.strip().endswith("failing tests:")), None)
    if start is None:
        return _node_tap_excerpts(lines)
    out = []
    for b in _node_failing_blocks(lines, start):
        out.extend(_node_block_keep(b))
    return out


def _cargo_stdout_blocks(lines):
    """The '---- name stdout ----' panic sections, 8 lines each at most."""
    out, i = [], 0
    while i < len(lines):
        if re.match(r"^---- .* stdout ----$", lines[i]):
            blk = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].strip() != "failures:" and not lines[i].startswith("----"):
                if lines[i].strip():
                    blk.append(lines[i].rstrip())
                i += 1
            out.extend(blk[:8])
        else:
            i += 1
    return out


def _cargo_failure_names(lines):
    """The indented test names listed under a 'failures:' heading."""
    out = []
    for k, line in enumerate(lines):
        if line.strip() == "failures:" and k + 1 < len(lines) and lines[k + 1].startswith("    "):
            j = k
            while j + 1 < len(lines) and (lines[j + 1].startswith("    ") or j == k):
                j += 1
                if lines[j].strip():
                    out.append(lines[j].rstrip())
    return out


def cargo_excerpts(lines):
    out = _cargo_stdout_blocks(lines) + _cargo_failure_names(lines)
    out.extend(line for line in lines if line.startswith("test result: FAILED"))
    return out


def generic_excerpts(lines):
    out: list = []
    for i, line in enumerate(lines):
        if FAILWORD.search(line):
            if i > 0 and lines[i - 1].rstrip() and lines[i - 1] not in out[-1:]:
                out.append(lines[i - 1].rstrip())
            out.append(line.rstrip())
    return out


def _test_env():
    """`test_env` of the nearest pipeline.json above the cwd, relative values made absolute against its `repo` folder; vars already set win; inside a job worktree the worktree is the root.
    Lets a Test line drop the `VAR=. cmd` prefix that the agents' sandbox denies."""
    d = os.getcwd()
    while True:
        path = os.path.join(d, "pipeline.json")
        if os.path.exists(path):
            break
        if os.path.dirname(d) == d:
            return {}
        d = os.path.dirname(d)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        root = os.path.join(d, cfg.get("repo") or "")
        parts = os.path.relpath(os.getcwd(), d).split(os.sep)
        if parts[0] == ".wt" and len(parts) >= 4:  # inside a job worktree `.wt/<stage>/<task>/<repo>`: that copy is the root, not the main checkout
            root = os.path.join(d, *parts[:4])
        return {k: os.path.normpath(os.path.join(root, str(v))) for k, v in (cfg.get("test_env") or {}).items() if k not in os.environ}
    except (OSError, ValueError, AttributeError):
        return {}


def _parallelize(cmd, cwd):
    """`python[3] [-I] -m unittest discover` (no other args) in a folder with ptest.py runs `python3 -I ptest.py` there instead (parallel). Any other arg (`-s`, `-k`, `-p`, ...) keeps the command: ptest runs its own default dir."""
    if len(cmd) < 4 or os.path.basename(cmd[0]) not in ("python", "python3"):
        return cmd
    rest = cmd[2:] if cmd[1] == "-I" else cmd[1:]
    if rest != ["-m", "unittest", "discover"]:
        return cmd
    if not os.path.isfile(os.path.join(cwd, "ptest.py")):
        return cmd
    print("qrun: unittest discover -> ptest (parallel)", file=sys.stderr)
    return ["python3", "-I", "ptest.py"]


def _timeout():
    """Seconds one run may take: env ORCH_SCRIPT_TIMEOUT (same name as the pipeline script steps), default SCRIPT_TIMEOUT_S."""
    return script_timeout()


def _descendants(pid):
    """-> pids of every process below PID (read from /proc; a Linux host)."""
    kids: dict = {}
    for e in os.listdir("/proc"):
        if e.isdigit():
            try:
                with open(f"/proc/{e}/stat", encoding="utf-8") as f:
                    kids.setdefault(int(f.read().rsplit(")", 1)[1].split()[1]), []).append(int(e))
            except (OSError, ValueError, IndexError):
                continue
    out, todo = [], [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            out.append(c)
            todo.append(c)
    return out


def _kill_tree(p):
    """Kill P and everything it started (also orphans, qrun is their subreaper), then reap it; return its output. P stays in qrun's own process group, so a kill of that group (a session timeout, Ctrl-C) reaches it too."""
    for _ in range(KILL_ROUNDS):
        pids = _descendants(os.getpid())  # P, its tree and the orphans that reparented to qrun
        if not pids:
            break
        for pid in pids:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
    try:
        return p.communicate(timeout=REAP_S)[0]
    except subprocess.TimeoutExpired:  # a grandchild that left the tree still holds the pipe
        return b""


def _on_term(_signum, _frame):
    raise SystemExit(128 + signal.SIGTERM)


def _become_subreaper():
    """Make qrun the parent of orphaned descendants (best effort: not on a host without prctl)."""
    with contextlib.suppress(AttributeError, ImportError, OSError):
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)


def _run(cmd, log):
    """Run CMD with its own TMPDIR (removed when it ends, also on a crash or kill), write the ANSI-stripped output to LOG; return (output lines, exit code). A timeout, SIGTERM or Ctrl-C kills the command and everything it started."""
    scratch = tempfile.mkdtemp(prefix=QRUN_TMP_PREFIX)
    try:
        return _run_in(cmd, log, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run_in(cmd, log, scratch):
    """The run of _run, with SCRATCH as the command's TMPDIR (a pipeline.json test_env TMPDIR still wins)."""
    _become_subreaper()
    try:
        p = subprocess.Popen(
            cmd, env={**os.environ, **QRUN_ENV, "TMPDIR": scratch, **_test_env()}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
    except OSError as e:
        raw, rc = (f"qrun: cannot run: {e}\n").encode(), 127
    else:
        old = signal.signal(signal.SIGTERM, _on_term)
        try:
            raw, rc = p.communicate(timeout=_timeout())[0], p.returncode
        except subprocess.TimeoutExpired:
            raw, rc = (_kill_tree(p) or b"") + f"qrun: timed out after {_timeout():g}s (ORCH_SCRIPT_TIMEOUT)\n".encode(), 124
        except BaseException:
            _kill_tree(p)
            raise
        finally:
            signal.signal(signal.SIGTERM, old)
    text = ANSI.sub("", raw.decode("utf-8", "replace"))
    with open(log, "w", encoding="utf-8") as f:
        f.write(text)
    return text.splitlines(), rc


def _report_ok(lines, sm, log):
    """Output lines and exit code for a command that exited 0."""
    if sm and (sm[1] == 0 or sm[2] > 0):
        why = "zero tests detected" if sm[1] == 0 else f"{sm[2]} skipped"
        return [f"SKIPPED/EMPTY: {why} ({sm[0]})", f"(log: {log})"], 3
    if sm:
        return [f"OK {sm[0]} (log: {log})"], 0
    return [f"OK exit 0 (log: {log})", *lines[-2:]], 0


def _failure_excerpts(lines, sm):
    ex = node_excerpts(lines) if sm and sm[0].startswith("node") else []
    if not ex:
        ex = cargo_excerpts(lines)
    if not ex:
        ex = generic_excerpts(lines) if any(FAILWORD.search(line) for line in lines) else lines[-15:]
    if not ex:
        ex = lines[-15:]
    return ex


def _report_fail(lines, sm, rc, log, max_lines):
    out = [f"FAIL exit {rc} (log: {log})"]
    ex = _failure_excerpts(lines, sm)
    room = max(max_lines - 2, 1)
    if len(ex) > room:
        more = len(ex) - (room - 1)
        ex = [*ex[: room - 1], f"... {more} more lines in {log}"]
    out.extend(ex)
    if sm:
        out.append(sm[0])
    return out


LOG_MAX_COUNT = QRUN_LOG_MAX_COUNT
LOG_MAX_AGE = QRUN_LOG_MAX_AGE_S  # logs (failures included) stay readable this long ...


def _prune_old_logs(directory, max_age=LOG_MAX_AGE, min_keep=QRUN_LOG_MIN_KEEP, max_count=LOG_MAX_COUNT):
    """Delete qrun-*.log regular files in DIRECTORY: all but the MAX_COUNT newest (fresh ones too, so a long run cannot fill /tmp); older than MAX_AGE seconds are also deleted, but the MIN_KEEP newest stay whatever their age (best effort)."""
    cutoff = time.time() - max_age
    logs = []
    with contextlib.suppress(OSError):
        for e in os.scandir(directory):
            if e.name.startswith("qrun-") and e.name.endswith(".log") and e.is_file(follow_symlinks=False):
                with contextlib.suppress(OSError):
                    logs.append((e.stat(follow_symlinks=False).st_mtime, e.path))
    logs.sort(reverse=True)
    for i, (mtime, path) in enumerate(logs):
        if i >= max_count or (i >= min_keep and mtime < cutoff):
            with contextlib.suppress(OSError):
                os.unlink(path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="qrun")
    ap.add_argument("--log")
    ap.add_argument("--max-lines", type=int, default=40)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        print("qrun: no command", file=sys.stderr)
        return 2
    # pid reuse must not overwrite an older log a report points to; follows TMPDIR, so a test run's scratch dir collects the logs
    log = a.log or os.path.join(tempfile.gettempdir(), f"qrun-{os.getpid()}-{time.time_ns()}.log")
    if not a.log:
        _prune_old_logs(tempfile.gettempdir())
    cmd = _parallelize(cmd, os.getcwd())
    lines, rc = _run(cmd, log)
    sm = summary(lines)
    if rc == 0:
        out, code = _report_ok(lines, sm, log)
    else:
        out, code = _report_fail(lines, sm, rc, log, a.max_lines), rc
    print("\n".join(out[: a.max_lines]))
    return code


if __name__ == "__main__":
    sys.exit(main())
