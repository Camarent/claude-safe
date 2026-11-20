"""codesrv nav requests: served from the warm pool (stub language server), exit paths close the pool, SIGKILL leaves no server."""

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import codesrv, lsp_client, lsp_pool
from tests.support.tmpdirs import tracked_mkdtemp

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = str(Path(__file__).parent / "support" / "fake_lsp.py")
STUB = {"py": ([sys.executable, "-I", FAKE], "python")}


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def gone_within(pid, secs):
    end = time.time() + secs
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def project():
    root = tracked_mkdtemp()
    Path(root, "pyproject.toml").write_text("")
    Path(root, "a.py").write_text("first\n")
    return root


class NavHandleTests(unittest.TestCase):
    def setUp(self):
        for name, val in (("STARTED", "v1"), ("LAST_USE", 0.0)):
            self.addCleanup(setattr, codesrv, name, getattr(codesrv, name, None))
            setattr(codesrv, name, val)
        p = mock.patch.dict(lsp_client.SERVERS, STUB)
        p.start()
        self.addCleanup(p.stop)
        self.pool = lsp_pool.Pool(interval=3600)
        self.addCleanup(self.pool.close_all)
        p2 = mock.patch.object(lsp_pool, "POOL", self.pool)
        p2.start()
        self.addCleanup(p2.stop)
        self.root = project()

    def call(self, req):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        a.sendall(json.dumps(req).encode() + b"\n")
        codesrv.handle(b)
        a.settimeout(5)
        return json.loads(a.recv(65536))

    def test_nav_served_without_ask_code(self):
        with mock.patch.dict(sys.modules, {"search.ask_code": None}):  # importing it would raise
            r = self.call({"argv": ["hover", "a.py", "1"], "ver": "v1", "kind": "nav", "cwd": self.root})
        self.assertEqual((r["out"].strip(), r["err"], r["code"]), ("v1:first", "", 0))
        self.assertNotIn("fallback", r)

    def test_nav_fallback(self):
        r = self.call({"argv": ["symbols", "x"], "ver": "v1", "kind": "nav", "cwd": self.root})
        self.assertTrue(r["fallback"])

    def test_no_kind_still_runs_find(self):
        with mock.patch("search.ask_code.main", return_value=0):
            r = self.call({"argv": ["x"], "ver": "v1"})
        self.assertEqual(r["code"], 0)
        self.assertNotIn("fallback", r)

    def test_files_and_limits(self):
        for f in ("lsp.py", "lsp_client.py", "lsp_pool.py"):
            self.assertIn(f, codesrv.FILES)
            self.assertTrue(os.path.exists(codesrv._tool_path(f)))
        self.assertLessEqual(codesrv.NAV_WAIT_S, codesrv.CODESRV_REQUEST_LIMIT_S - 5)
        assert lsp_client.Client.req.__defaults__ is not None
        self.assertLessEqual(lsp_client.Client.req.__defaults__[0], codesrv.NAV_WAIT_S)

    def test_slow_pool_falls_back_before_watchdog(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        with (
            mock.patch.object(codesrv, "NAV_WAIT_S", 0.2),
            mock.patch.object(lsp_pool, "nav", lambda a, c: gate.wait(10)),
        ):
            r = self.call({"argv": ["hover", "a.py", "1"], "ver": "v1", "kind": "nav", "cwd": self.root})
        self.assertTrue(r["fallback"])

    def test_close_pool_stops_servers(self):
        self.call({"argv": ["hover", "a.py", "1"], "ver": "v1", "kind": "nav", "cwd": self.root})
        pids = self.pool.pids()
        self.assertEqual(len(pids), 1)
        codesrv.close_pool()
        self.assertTrue(gone_within(pids[0], 5))

    def test_restart_and_watchdog_close_first(self):
        with mock.patch.object(codesrv.os, "_exit") as ex, mock.patch.object(codesrv, "close_pool") as cp:
            codesrv._exit(7)
            codesrv._watchdog_fire()
        self.assertEqual(cp.call_count, 2)
        self.assertEqual([c.args[0] for c in ex.call_args_list], [7, 1])


class FreshImportTests(unittest.TestCase):
    def test_import_order_either_way(self):
        for first in ("search.codesrv", "search.lsp_pool"):
            code = f"import sys; sys.path.insert(0, {TOOLS!r}); import {first}; import search.codesrv, search.lsp_pool"
            r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)


RUNNER = """
import sys
sys.path.insert(0, {tools!r})
from search import lsp_client, codesrv
lsp_client.SERVERS["py"] = ([sys.executable, "-I", {fake!r}], "python")
codesrv.main()
"""


class SubprocessServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sock = os.path.join(self.tmp.name, "s.sock")
        self.root = project()

    def start(self, **env):
        e = dict(os.environ, CODESRV_SOCK=self.sock, **env)
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-c", RUNNER.format(tools=TOOLS, fake=FAKE)],
            env=e,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(self.reap)
        end = time.time() + 30
        while not os.path.exists(self.sock) and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.sock))

    def reap(self):
        with contextlib.suppress(OSError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait(10)

    def nav(self):
        deadline = time.monotonic() + 30
        while True:  # the socket file appears at bind(), before listen(): retry until the server accepts
            c = socket.socket(socket.AF_UNIX)
            c.settimeout(30)
            try:
                c.connect(self.sock)
                break
            except (ConnectionRefusedError, FileNotFoundError):
                c.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        req = {"argv": ["hover", "a.py", "1"], "ver": codesrv.version(), "kind": "nav", "cwd": self.root}
        c.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(65536)
            if not chunk:
                break
            buf += chunk
        c.close()
        return json.loads(buf)

    def server_pid(self):
        out = subprocess.run(["pgrep", "-g", str(self.proc.pid), "-f", "fake_lsp"], capture_output=True, text=True, check=False)
        pids = [int(x) for x in out.stdout.split() if int(x) != self.proc.pid]  # the runner's own -c text names the stub too
        self.assertEqual(len(pids), 1, out.stdout)
        return pids[0]

    def test_sigkill_leaves_no_language_server(self):
        self.start()
        self.assertEqual(self.nav()["out"].strip(), "v1:first")
        pid = self.server_pid()
        os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(10)
        self.assertTrue(gone_within(pid, 5))

    def test_idle_exit_leaves_no_language_server(self):
        self.start(CODESRV_IDLE="1")
        self.nav()
        pid = self.server_pid()
        self.assertEqual(self.proc.wait(30), 0)
        self.assertTrue(gone_within(pid, 5))

    def test_sigterm_closes_pool(self):
        self.start()
        self.nav()
        pid = self.server_pid()
        self.proc.terminate()
        self.proc.wait(10)
        self.assertTrue(gone_within(pid, 5))


if __name__ == "__main__":
    unittest.main()
