#!/usr/bin/env python3
"""Warm background server for `local.py find` and `local.py index`. Started by local.py on first use; nobody runs it by hand.

Keeps the embedding model loaded (about 2.5 s saved per call) and answers requests over a unix socket, several at once.
Exits by itself after CODESRV_IDLE seconds (default 1800) without requests, or when the tool files changed (the next call starts a fresh one).
Run under the venv python that has fastembed. Log: /tmp/codesrv.log. One request = one JSON line {"argv": [...], "ver": "..."}; reply = one JSON line {"out","err","code"}.
Kind "nav" ({"kind": "nav", "argv", "cwd"}) runs a language-server op on the warm pool; the reply adds "fallback": true when the pool cannot answer."""

import atexit
import contextlib
import io
import json
import os
import signal
import socket
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
from pipeline.configuration import CODESRV_REQUEST_LIMIT_S  # noqa: E402

SOCK = os.environ.get("CODESRV_SOCK", f"/tmp/codesrv-{os.getuid()}.sock")  # nosec  # per-uid path in a single-user container
IDLE = int(os.environ.get("CODESRV_IDLE", "1800"))
FILES = (
    "ask_code.py",
    "codesrv.py",
    "lsp.py",
    "lsp_client.py",
    "lsp_pool.py",
    "strata.py",
    "localq.py",
    os.path.join("localmodel", "askmodel.py"),
)
NAV_WAIT_S = max(1.0, CODESRV_REQUEST_LIMIT_S - 5)  # a nav call answers or falls back before the watchdog fires
tl = threading.local()
STARTED = ""  # set by main(): the tool-files version this server started with
LAST_USE = 0.0  # time of the last request


def _tool_path(name):
    own = os.path.join(HERE, name)
    return own if os.path.exists(own) else os.path.join(TOOLS, name)


def version():
    return str(max(os.path.getmtime(p) for p in map(_tool_path, FILES) if os.path.exists(p)))


class Router:
    """sys.stdout/sys.stderr stand-in that writes to the calling thread's request buffer."""

    def __init__(self, name, real):
        self.name, self.real = name, real

    def write(self, s):
        return (getattr(tl, self.name, None) or self.real).write(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def close_pool():
    """Stop every pooled language server. Never imports the pool: no pool loaded, nothing to stop."""
    pool = sys.modules.get("search.lsp_pool")
    if pool is not None:
        with contextlib.suppress(Exception):
            pool.close_all()


def _exit(code):
    close_pool()
    os._exit(code)


def _on_signal(signum, _frame):
    close_pool()
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCK)
    os._exit(128 + signum)


def _watchdog_fire():
    print(f"codesrv watchdog: a request ran over {CODESRV_REQUEST_LIMIT_S}s; exiting", file=sys.__stderr__, flush=True)
    _exit(1)


def _nav(req):
    """Serve a nav request from the warm pool (lazy import: lsp_pool imports this module). Reply dict; fallback=true when the pool cannot answer."""
    from search import lsp_pool

    box = {}

    def work():
        try:
            box["r"] = lsp_pool.nav(req["argv"], req.get("cwd") or os.getcwd())
        except BaseException as e:  # Fallback or a bug: either way the caller runs the one-shot path
            box["e"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(NAV_WAIT_S)
    if "r" not in box:
        if "e" in box and not isinstance(box["e"], lsp_pool.Fallback):
            traceback.print_exception(box["e"], file=sys.__stderr__)
        return {"out": "", "err": "", "code": 0, "fallback": True}
    out, err, code = box["r"]
    return {"out": out, "err": err, "code": code}


def handle(conn):
    dog = None
    try:
        line = conn.makefile("rb").readline()
        req = json.loads(line)
        global LAST_USE
        LAST_USE = time.time()
        if req.get("ver") != STARTED:  # tool files changed since this server started
            conn.sendall(b'{"restart": true}\n')
            threading.Timer(0.2, lambda: _exit(0)).start()
            return
        dog = threading.Timer(CODESRV_REQUEST_LIMIT_S, _watchdog_fire)
        dog.daemon = True
        dog.start()
        if req.get("kind") == "nav":
            conn.sendall((json.dumps(_nav(req)) + "\n").encode())
            return
        tl.out, tl.err = io.StringIO(), io.StringIO()
        from search import ask_code

        code = 0
        try:
            code = ask_code.main(req["argv"])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (print(e.code, file=sys.stderr) or 2)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            code = 3
        conn.sendall((json.dumps({"out": tl.out.getvalue(), "err": tl.err.getvalue(), "code": code or 0}) + "\n").encode())
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if dog:
            dog.cancel()
        tl.out = tl.err = None
        conn.close()
        LAST_USE = time.time()


def main():
    global STARTED, LAST_USE
    if any(x in ("-h", "--help") for x in sys.argv[1:]):
        sys.exit(__doc__)  # never start (or steal the socket of) a server just to print usage
    STARTED = version()
    LAST_USE = time.time()
    sys.stdout, sys.stderr = Router("out", sys.__stdout__), Router("err", sys.__stderr__)
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(SOCK)
        print("another server already answers on the socket; exiting", file=sys.__stderr__)
        return  # never run two
    except OSError:
        pass
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX)
    old_umask = os.umask(0o177)  # bind creates the socket 0600: no window at the umask mode before a chmod
    try:
        srv.bind(SOCK)
    finally:
        os.umask(old_umask)
    os.chmod(SOCK, 0o600)
    atexit.register(close_pool)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
    srv.listen(16)
    srv.settimeout(5)
    from search import ask_code

    threading.Thread(
        target=lambda: ask_code.embedder() and list(ask_code.embedder().query_embed(["warm up"])), daemon=True
    ).start()  # load the model now, not on the first request
    print(f"codesrv up pid {os.getpid()} version {STARTED}", file=sys.__stderr__, flush=True)
    while True:
        try:
            conn, _ = srv.accept()
        except TimeoutError:
            if time.time() - LAST_USE > IDLE:
                break
            continue
        threading.Thread(target=handle, args=(conn,), daemon=True).start()
    close_pool()
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCK)


if __name__ == "__main__":
    main()
