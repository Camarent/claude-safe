"""codesrv: request handling over a socketpair, and the whole server on a temp unix socket (always shut down, socket deleted)."""

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code, codesrv
from tests.support.timing import timing_sensitive


def _restore(saved):
    sys.stdout, sys.stderr = saved


class HandleTests(unittest.TestCase):
    def setUp(self):
        saved = (sys.stdout, sys.stderr)
        self.addCleanup(_restore, saved)
        sys.stdout, sys.stderr = codesrv.Router("out", sys.__stdout__), codesrv.Router("err", sys.__stderr__)
        for name, val in (("STARTED", "v1"), ("LAST_USE", 0.0)):
            self.addCleanup(setattr, codesrv, name, getattr(codesrv, name, None))
            setattr(codesrv, name, val)

    def call(self, raw):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        a.sendall(raw)
        codesrv.handle(b)
        a.settimeout(2)
        return a.recv(65536)

    def req(self, argv, ver="v1"):
        return json.dumps({"argv": argv, "ver": ver}).encode() + b"\n"

    def test_version_helper(self):
        self.assertEqual(float(codesrv.version()), max(os.path.getmtime(codesrv._tool_path(f)) for f in codesrv.FILES))

    def test_router(self):
        r = codesrv.Router("out", io.StringIO())
        self.assertFalse(r.isatty())
        self.assertIsNone(r.flush())
        self.assertEqual(r.write("abc"), 3)  # no thread buffer: falls to the real stream
        codesrv.tl.out = io.StringIO()
        self.addCleanup(setattr, codesrv.tl, "out", None)
        r.write("xy")
        self.assertEqual(codesrv.tl.out.getvalue(), "xy")

    def test_ok(self):
        def fake_main(argv):
            print("out line")
            print("err line", file=sys.stderr)
            return 1

        with mock.patch.object(ask_code, "main", side_effect=fake_main):
            r = json.loads(self.call(self.req(["q"])))
        self.assertEqual(r, {"out": "out line\n", "err": "err line\n", "code": 1})
        self.assertGreater(codesrv.LAST_USE, 0)

    def test_none_code_becomes_zero(self):
        with mock.patch.object(ask_code, "main", return_value=None):
            self.assertEqual(json.loads(self.call(self.req([])))["code"], 0)

    def test_system_exit_int_and_text(self):
        with mock.patch.object(ask_code, "main", side_effect=SystemExit(4)):
            self.assertEqual(json.loads(self.call(self.req([])))["code"], 4)
        with mock.patch.object(ask_code, "main", side_effect=SystemExit("usage text")):
            r = json.loads(self.call(self.req([])))
        self.assertEqual((r["code"], r["err"]), (2, "usage text\n"))

    def test_exception_is_code_3_with_traceback(self):
        with mock.patch.object(ask_code, "main", side_effect=RuntimeError("boom")):
            r = json.loads(self.call(self.req([])))
        self.assertEqual(r["code"], 3)
        self.assertIn("RuntimeError: boom", r["err"])

    def test_version_mismatch_asks_restart(self):
        with mock.patch.object(codesrv.threading, "Timer") as timer:
            self.assertEqual(self.call(self.req(["q"], ver="old")), b'{"restart": true}\n')
        timer.assert_called_once()
        timer.return_value.start.assert_called_once()

    def test_garbage_request_is_swallowed(self):
        with mock.patch.object(codesrv.traceback, "print_exc") as pe:
            self.assertEqual(self.call(b"not json\n"), b"")
        pe.assert_called_once()


def wait_for_path(path, within=30):
    end = time.monotonic() + within
    while not os.path.exists(path) and time.monotonic() < end:
        time.sleep(0.02)


@timing_sensitive
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sock = os.path.join(self.tmp, "s.sock")
        saved = (sys.stdout, sys.stderr)
        self.addCleanup(_restore, saved)
        for name in ("SOCK", "IDLE"):
            self.addCleanup(setattr, codesrv, name, getattr(codesrv, name))
        codesrv.SOCK = self.sock
        self.thread = None
        self.addCleanup(self.stop)

        real = socket.socket

        class Quick(real):  # type: ignore[valid-type,misc]  # real = the original socket class
            def settimeout(self, _t):
                super().settimeout(0.1)

        p = mock.patch.object(codesrv.socket, "socket", Quick)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(ask_code, "embedder", return_value=None)
        p2.start()
        self.addCleanup(p2.stop)

    def stop(self):
        codesrv.IDLE = -1  # idle check fires at the next accept timeout
        if self.thread:
            self.thread.join(30)
            self.assertFalse(self.thread.is_alive())
        if os.path.exists(self.sock):
            os.unlink(self.sock)

    def ask(self, payload):
        c = socket.socket(socket.AF_UNIX)
        c.settimeout(30)
        c.connect(self.sock)
        c.sendall(payload)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(65536)
            if not chunk:
                break
            buf += chunk
        c.close()
        return buf

    def test_serves_one_request_then_exits_idle(self):
        codesrv.IDLE = 1800
        self.thread = threading.Thread(target=codesrv.main, daemon=True)
        with mock.patch.object(ask_code, "main", return_value=0):
            self.thread.start()
            wait_for_path(self.sock)
            self.assertEqual(oct(os.stat(self.sock).st_mode & 0o777), "0o600")
            r = json.loads(self.ask(json.dumps({"argv": ["x"], "ver": codesrv.STARTED}).encode() + b"\n"))
            self.assertEqual(r["code"], 0)
            codesrv.IDLE = -1
            self.thread.join(30)
        self.assertFalse(self.thread.is_alive())
        self.assertFalse(os.path.exists(self.sock))  # removed on the way out

    def test_socket_is_private_from_the_moment_it_appears(self):
        # no chmod: the mode must come from bind itself, else the socket is visible at umask mode until chmod runs
        codesrv.IDLE = 1800
        self.thread = threading.Thread(target=codesrv.main, daemon=True)
        with mock.patch.object(codesrv.os, "chmod"):
            self.thread.start()
            wait_for_path(self.sock)
            self.assertEqual(oct(os.stat(self.sock).st_mode & 0o777), "0o600")

    def test_second_server_exits_when_one_answers(self):
        other = socket.socket(socket.AF_UNIX)
        self.addCleanup(other.close)
        other.bind(self.sock)
        other.listen(1)
        self.assertIsNone(codesrv.main())  # returns at once, leaves the socket alone
        self.assertTrue(os.path.exists(self.sock))
        os.unlink(self.sock)


if __name__ == "__main__":
    unittest.main()


@timing_sensitive
class StuckServerTests(unittest.TestCase):
    """A server that never replies: daemon_call times out, kills it, and returns None (caller runs in-process)."""

    def test_timeout_kills_server_and_gives_up(self):
        import subprocess

        from search import local

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        sock = os.path.join(d, "s.sock")
        code = "import socket,sys,time\ns=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1]);s.listen(4)\nwhile True:\n c,_=s.accept();time.sleep(60)"
        p = subprocess.Popen([sys.executable, "-I", "-c", code, sock])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        wait_for_path(sock)
        with (
            mock.patch.object(local, "SOCK", sock),
            mock.patch.object(local, "CODESRV_REPLY_TIMEOUT_S", 0.3),
            mock.patch.object(local, "srv_start", return_value=None),
        ):
            self.assertIsNone(local.daemon_call(["x"]))
        self.assertIsNotNone(p.wait(timeout=30))
        self.assertFalse(os.path.exists(sock))
