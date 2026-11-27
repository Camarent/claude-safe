#!/usr/bin/env python3
"""One entry point for the local model and the code-navigation tools. Agents call this and nothing else.

  local.py find "casual question about the code" [--root DIR] [--no-model]   cited file:line hits or IDK (index + model judge)
  local.py index [--root DIR] [--reindex]                                    build the whole search index now (chunks + embeddings; ~3 min per 5000 chunks, once)
  local.py nav def|refs|hover FILE LINE[:COL]|SYMBOL                         exact, no model: language server
  local.py nav outline|diag FILE | symbols NAME [--lang py|js|ts|cpp|rs]     exact, no model
  local.py judge --criteria "text or @file" FILE [FILE ...]                  PASS/FAIL per file with a quoted line as evidence
  local.py review --brief FILE --diff FILE [--tests FILE]                    strict diff review against a brief, ONE call over the whole diff: PASS/FAIL + every finding
  local.py judgefiles --criteria FILE --files FILE                           the deliverable files text (`=== path ===` headers, `N: ` lines) against Done criteria, ONE call
  local.py bulk|pack|code|log ...                                            passed through to localdev.py (long reads, narrow code tasks)
  local.py status                                                            who holds which model slot

Every model call waits for a free slot in localq (LOCALQ_CALLS, default 1; whole `code` jobs use LOCALQ_JOBS), so many agents
can call this at once without piling onto the GPU. `nav` never touches the model or the queue.
Exit codes: 0 ok, 1 IDK / FAIL verdict, 2 usage, 3 server problem."""

import contextlib
import json
import os
import signal
import socket
import struct
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
from localmodel.prompts import REVIEW_FINDINGS_ANSWER, VERDICT_ANSWER, findings_lines  # noqa: E402
from pipeline.configuration import (  # noqa: E402
    ASK_VENV_PY,
    CODESRV_REPLY_TIMEOUT_S,
    LOCAL_ANSWER_TOKENS,
    PREREVIEW_FINDINGS_MAX,
    PREREVIEW_INPUT_TOKENS,
    REVIEW_SKIP_RC,
)
from pipeline.util import est_tokens, text_arg  # noqa: E402

from search.codesrv import FILES, NAV_WAIT_S  # noqa: E402  # the server and this client watch the same files


def tool_path(name):
    """-> the file `name`: a sibling in search/, else relative to the tools root (localmodel/...)."""
    own = os.path.join(HERE, name)
    return own if os.path.exists(own) else os.path.join(TOOLS, name)


def script(name, args):
    return subprocess.call([sys.executable, "-I", tool_path(name), *args])


def opt(a, flag, default=None):
    return a[a.index(flag) + 1] if flag in a else default


def reexec_venv(argv):
    """The embedding library lives in its own venv (the system python stays clean); run find/index under it when present."""
    py = os.environ.get("ASK_CODE_PY", ASK_VENV_PY)
    if os.path.exists(py) and not os.environ.get("ASK_CODE_REEXEC"):
        os.environ["ASK_CODE_REEXEC"] = "1"
        os.execv(py, [py, "-I", os.path.abspath(__file__), *argv])


SOCK = os.environ.get("CODESRV_SOCK", f"/tmp/codesrv-{os.getuid()}.sock")  # nosec  # per-uid path in a single-user container
LOCK = f"{SOCK}.lock" if "CODESRV_SOCK" in os.environ else f"/tmp/codesrv-{os.getuid()}.lock"  # nosec  # per-uid path in a single-user container
NAV_REPLY_MARGIN_S = 10.0  # the client waits this much longer than the server's nav wait
NAV_OPS = ("def", "refs", "hover", "outline", "diag")


def srv_version():
    return str(max(os.path.getmtime(tool_path(f)) for f in FILES if os.path.exists(tool_path(f))))


def srv_connect():
    c = socket.socket(socket.AF_UNIX)
    try:
        c.connect(SOCK)
        return c
    except OSError:
        c.close()
        return None


def srv_kill(c):
    """Kill the server behind the connected socket `c` (pid from SO_PEERCRED) and remove its socket path."""
    with contextlib.suppress(OSError):
        pid = struct.unpack("3i", c.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
        os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(FileNotFoundError, OSError):
        os.unlink(SOCK)


def srv_start():
    """Start the warm server (it keeps the embedding model loaded between calls). None if it cannot run here.
    A lock makes parallel first calls start exactly one."""
    import fcntl
    import time

    py = os.environ.get("ASK_CODE_PY", ASK_VENV_PY)
    if not os.path.exists(py):
        return None
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        c = srv_connect()
        if c:
            return c  # someone else started it while we waited for the lock
        with contextlib.suppress(FileNotFoundError):
            os.unlink(SOCK)
        log = open("/tmp/codesrv.log", "ab")  # noqa: SIM115  # handle outlives this function  # nosec  # per-uid path in a single-user container
        subprocess.Popen([py, "-I", os.path.join(HERE, "codesrv.py")], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(100):
            c = srv_connect()
            if c:
                return c
            time.sleep(0.2)
    return None


def _request(argv, kind, cwd):
    req = {"argv": argv, "ver": srv_version()}
    if kind == "nav":
        req.update(kind="nav", cwd=cwd or os.getcwd())
    return req


def _recv_reply(c):
    """One newline-terminated JSON reply from the server -> dict, or None when the connection closes first."""
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = c.recv(65536)
        if not chunk:
            return None
        buf += chunk
    return json.loads(buf)


def _exchange(c, argv, kind, cwd):
    """Send one request on connection `c` and read its reply (None when the server closes first)."""
    c.settimeout(NAV_WAIT_S + NAV_REPLY_MARGIN_S if kind == "nav" else CODESRV_REPLY_TIMEOUT_S)
    c.sendall(json.dumps(_request(argv, kind, cwd)).encode() + b"\n")
    return _recv_reply(c)


def daemon_call(argv, kind="find", cwd=None):
    """Run an ask_code command (kind find) or a language-server op (kind nav) in the warm server. Returns its exit code, or None when the server is unavailable or a nav reply says fallback (caller then runs it in-process). A nav timeout never kills the server."""
    nav = kind == "nav"
    timed_out = False
    for _ in range(2):
        c = srv_connect() or srv_start()
        if c is None:
            return None
        try:
            r = _exchange(c, argv, kind, cwd)
        except TimeoutError:  # the server is stuck: kill it, try once with a fresh one, then give up (caller runs in-process)
            if nav:
                return None  # a slow language server must not take `find` down with it
            srv_kill(c)
            if timed_out:
                return None
            timed_out = True
            continue
        except (OSError, ValueError):  # server gone or garbled reply: None makes the caller run in-process
            return None
        finally:
            c.close()
        if r is None:  # server closed before a full reply
            return None
        if r.get("restart"):  # the server was older than the tool files: it exits, we start a fresh one
            import time

            time.sleep(0.5)
            continue
        if r.get("fallback"):
            return None
        sys.stdout.write(r["out"])
        sys.stderr.write(r["err"])
        sys.stdout.flush()
        return r["code"]
    return None


def with_root(a):
    if "--root" in a:
        return a
    from search import ask_code

    return [*a, "--root", ask_code.find_root()]


def cmd_nav(a):
    """nav def|refs|hover|outline|diag via the warm server; symbols, LOCAL_NODAEMON=1 and any server trouble run lsp.py per call."""
    if a and a[0] in NAV_OPS and not os.environ.get("LOCAL_NODAEMON"):
        rc = daemon_call(a, kind="nav", cwd=os.getcwd())
        if rc is not None:
            return rc
    return script("lsp.py", a)


def cmd_find(a):
    if not os.environ.get("LOCAL_NODAEMON"):
        rc = daemon_call(with_root(a))
        if rc is not None:
            return rc
    reexec_venv(["find", *a])
    from search import ask_code

    return ask_code.main(a)


def cmd_index(a):
    if not os.environ.get("LOCAL_NODAEMON"):
        rc = daemon_call(with_root(["--index", *a]))
        if rc is not None:
            return rc
    reexec_venv(["index", *a])
    from search import ask_code

    return ask_code.main(["--index", *a])


JUDGE_FILE_CHARS = 12000  # `lp judge` reads this many chars of a file; it says so when the file is longer
JUDGE_TOKENS = 3500  # reasoning on: the thinking tokens come out of this budget, a smaller cap left empty answers


def cmd_judge(a):
    from localmodel import askmodel

    crit = text_arg(opt(a, "--criteria") or sys.exit("judge needs --criteria"))
    files = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or a[i - 1] != "--criteria")]
    worst = 0
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            whole = fh.read()
        body = whole[:JUDGE_FILE_CHARS]
        if len(whole) > JUDGE_FILE_CHARS:
            print(f"note: {f} judged only the first {JUDGE_FILE_CHARS} of {len(whole)} chars")
        j = askmodel.jsonobj(
            askmodel.llm(
                f"Criteria:\n{crit}\n\nFile {f}:\n{body}\n\nDoes the file meet ALL the criteria? Be strict. If PASS or FAIL hinges on a specific line, copy that line exactly. "
                + VERDICT_ANSWER,
                JUDGE_TOKENS,
                think=True,
            )
        )
        v = j.get("verdict", "?")
        ev = str(j.get("evidence", "")).strip()
        shown = ev if ev and " ".join(ev.split()) in " ".join(body.split()) else ""  # drop quotes that are not in the file
        print(f"{v}  {f}  {j.get('reason', '')}" + (f"  | {shown}" if shown else ""))
        worst = max(worst, 0 if v == "PASS" else 1)
    return worst


RULES = (
    "You are a strict code reviewer. Review the diff against the task brief.\n"
    "PASS only if ALL hold: every change matches the brief (nothing missing, nothing extra); a file outside the brief's predicted paths is fine when the brief's goal needs it (never FAIL for a missing `Extra files:` entry; only a side refactor is extra); no test file is weakened (an edit to a test that the change breaks by design, such as a rename or a label change, is allowed); "
    "the implementation is genuine (it does not hard-code the tested inputs); every detail the brief states (names, wording, order) is exactly right; the tests pass.\n"
    "Otherwise FAIL, and quote each offending diff line with its file and line number. " + REVIEW_FINDINGS_ANSWER
)


def too_big(text):
    """-> the `skipped:` line when `text` is over the one-call token budget, else None."""
    n = est_tokens(text)
    return f"skipped: input ~{n} est. tokens > budget {PREREVIEW_INPUT_TOKENS}: not reviewed" if n > PREREVIEW_INPUT_TOKENS else None


def one_call(prompt):
    """ONE model call over the whole `prompt`; prints PASS or FAIL + one `why  | where quote` line per finding. -> exit code."""
    from localmodel import askmodel

    j = askmodel.jsonobj(askmodel.llm(prompt, LOCAL_ANSWER_TOKENS, think=True))
    v = j.get("verdict", "?")
    lines = findings_lines(j, PREREVIEW_FINDINGS_MAX) if v != "PASS" else []
    print(f"{v}  {j.get('reason', '')}" + "".join("\n" + ln for ln in lines))
    return 0 if v == "PASS" else 1


def cmd_review(a):
    brief = Path(opt(a, "--brief") or sys.exit("review needs --brief")).read_text(encoding="utf-8")
    diff = Path(opt(a, "--diff") or sys.exit("review needs --diff")).read_text(encoding="utf-8")
    tests = Path(opt(a, "--tests")).read_text(encoding="utf-8") if "--tests" in a else "(not provided)"
    prompt = f"{RULES}\n\n## Brief\n{brief}\n## Test results\n{tests}\n\n## Diff\n{diff}"
    if msg := too_big(prompt):
        print(msg)
        return REVIEW_SKIP_RC
    return one_call(prompt)


FILES_RULES = (
    "You review the deliverable files of a task against its Done criteria. Each file starts with a `=== path ===` line; its lines carry `N: ` numbers. "
    "Do not judge totals, counts, or anything the task's validator script checks. "
    "FAIL when a shown line violates a Done criterion: quote that line exactly as `file:line text`. Otherwise PASS. "
)


def cmd_judgefiles(a):
    """`lp judgefiles --criteria F --files F`: all deliverable files (one text) against the Done criteria, ONE call."""
    crit = Path(opt(a, "--criteria") or sys.exit("judgefiles needs --criteria")).read_text(encoding="utf-8")
    body = Path(opt(a, "--files") or sys.exit("judgefiles needs --files")).read_text(encoding="utf-8")
    prompt = f"{FILES_RULES}\n\n## Done criteria\n{crit}\n\n## Files\n{body}\n\n" + REVIEW_FINDINGS_ANSWER
    if msg := too_big(prompt):
        print(msg)
        return REVIEW_SKIP_RC
    return one_call(prompt)


def _local_model_cmd(c, rest):
    """judge / review / judgefiles: skipped (rc 0) when the local model is down or off, else run."""
    from search.strata import strata_up

    if not strata_up():
        print(f"skipped: local model down or off ({c}); local-model steps are optional")
        return 0
    return {"judge": cmd_judge, "review": cmd_review, "judgefiles": cmd_judgefiles}[c](rest)


def main(a):
    if not a or a[0] in ("-h", "--help"):
        sys.exit(__doc__)
    c, rest = a[0], a[1:]
    if c == "nav":
        return cmd_nav(rest)
    if c == "find":
        return cmd_find(rest)
    if c == "index":
        return cmd_index(rest)
    if c in ("judge", "review", "judgefiles"):
        return _local_model_cmd(c, rest)
    if c in ("bulk", "pack", "code", "log"):
        return script("localmodel/localdev.py", a)
    if c == "status":
        return script("localq.py", ["status"])
    sys.exit(__doc__)


class _Count:
    """stdout wrapper: counts the lines written, passes everything through."""

    def __init__(self, f):
        self.f, self.lines = f, 0

    def write(self, t):
        self.lines += t.count("\n")
        return self.f.write(t)

    def __getattr__(self, n):
        return getattr(self.f, n)


def usage_log(argv, rc, seconds, lines):
    """One JSON line per call in LP_USAGE_LOG (default ~/.claude/lp-usage.jsonl). Never raises."""
    try:
        import time

        path = os.environ.get("LP_USAGE_LOG") or os.path.expanduser("~/.claude/lp-usage.jsonl")
        rec = {
            "ts": int(time.time()),
            "cmd": " ".join(argv[:2]) if argv and argv[0] == "nav" else (argv[0] if argv else ""),
            "root": os.getcwd(),
            "seconds": round(seconds, 2),
            "rc": rc,
            "lines": lines,
            "idk": rc == 1 and bool(argv) and argv[0] == "find",
            "q": (argv[1] if len(argv) > 1 and argv[0] == "find" else "")[:200],
        }
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except (OSError, TypeError, ValueError):  # a log line never fails the search
        pass


def run_logged(argv):
    import time

    t0, out, rc = time.time(), _Count(sys.stdout), 1
    sys.stdout = out
    try:
        rc = main(argv)
        return rc
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
        raise
    finally:
        sys.stdout = out.f
        usage_log(argv, rc if isinstance(rc, int) else 1, time.time() - t0, out.lines)


if __name__ == "__main__":
    sys.exit(run_logged(sys.argv[1:]))
