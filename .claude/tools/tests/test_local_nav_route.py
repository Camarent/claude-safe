"""local.py nav routing: warm server first, per-call lsp.py on fallback, no server kill on a nav timeout."""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from search import local  # noqa: E402


class FakeServer:
    """One-shot server on a unix socket in a temp dir; records the request, sends `reply` (None = say nothing)."""

    def __init__(self, path, reply, hold=0.0):
        self.reply, self.req, self.hold = reply, None, hold
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(1)
        self.t = threading.Thread(target=self.run)
        self.t.start()

    def run(self):
        c, _ = self.srv.accept()
        with c:
            buf = b""
            while not buf.endswith(b"\n"):
                buf += c.recv(65536)
            self.req = json.loads(buf)
            if self.reply is None:
                c.settimeout(self.hold)
                with contextlib.suppress(OSError):
                    c.recv(1)  # wait until the client gives up
            else:
                c.sendall(json.dumps(self.reply).encode() + b"\n")

    def close(self):
        self.t.join(10)
        self.srv.close()


class NavRoute(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = td.name
        self.sock = os.path.join(self.tmp, "s.sock")

    def serve(self, reply, hold=5.0):
        s = FakeServer(self.sock, reply, hold)
        self.addCleanup(s.close)
        return s

    def run_nav(self, argv, script_rc=0):
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "LOCAL_NODAEMON"}
        with (
            mock.patch.object(local, "SOCK", self.sock),
            mock.patch.object(local, "srv_start", return_value=None),
            mock.patch.object(local, "script", return_value=script_rc) as sc,
            mock.patch.object(local, "srv_kill") as kill,
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = local.main(["nav", *argv])
        return rc, out.getvalue(), err.getvalue(), sc, kill

    def test_server_answers(self):
        for code in (0, 2, 3):
            with self.subTest(code=code):
                if os.path.exists(self.sock):
                    os.unlink(self.sock)
                s = self.serve({"out": "o\n", "err": "e\n", "code": code})
                rc, out, err, sc, _ = self.run_nav(["def", "a.py", "3"], script_rc=99)
                self.assertEqual((rc, out, err), (code, "o\n", "e\n"))
                sc.assert_not_called()
                self.assertEqual((s.req["kind"], s.req["argv"], s.req["cwd"]), ("nav", ["def", "a.py", "3"], os.getcwd()))

    def test_fallback_flag_runs_script_without_server_output(self):
        self.serve({"out": "SERVER\n", "err": "SERR\n", "code": 5, "fallback": True})
        rc, out, err, sc, kill = self.run_nav(["diag", "a.py"], script_rc=3)
        self.assertEqual((rc, out, err), (3, "", ""))
        sc.assert_called_once_with("lsp.py", ["diag", "a.py"])
        kill.assert_not_called()

    def test_no_server(self):
        rc, _, _, sc, _ = self.run_nav(["hover", "a.py", "1"], script_rc=2)
        self.assertEqual(rc, 2)
        sc.assert_called_once()

    def test_bad_json_and_closed(self):
        self.serve(None, hold=0.0)  # closes without a reply
        rc, _, _, sc, _ = self.run_nav(["outline", "a.py"], script_rc=0)
        self.assertEqual(rc, 0)
        sc.assert_called_once()

    def test_timeout_falls_back_without_kill(self):
        self.serve(None, hold=5.0)
        with mock.patch.object(local, "NAV_WAIT_S", 0.2), mock.patch.object(local, "NAV_REPLY_MARGIN_S", 0.2):
            rc, _, _, sc, kill = self.run_nav(["refs", "a.py", "x"], script_rc=3)
        self.assertEqual(rc, 3)
        kill.assert_not_called()
        sc.assert_called_once_with("lsp.py", ["refs", "a.py", "x"])

    def test_symbols_and_nodaemon_skip_server(self):
        with mock.patch.object(local, "daemon_call") as dc, mock.patch.object(local, "script", return_value=0) as sc:
            local.main(["nav", "symbols", "X"])
            with mock.patch.dict(os.environ, {"LOCAL_NODAEMON": "1"}):
                local.main(["nav", "def", "a.py", "1"])
        dc.assert_not_called()
        self.assertEqual(sc.call_count, 2)

    def test_timeout_margin(self):
        self.assertGreater(local.NAV_WAIT_S + local.NAV_REPLY_MARGIN_S, local.NAV_WAIT_S)

    def test_env_sock_and_lock(self):
        code = "from search import local; print(local.SOCK); print(local.LOCK)"
        env = {**os.environ, "CODESRV_SOCK": "/x/y.sock"}
        r = subprocess.run(
            [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(HERE.parent)!r}); {code}"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(r.stdout.split(), ["/x/y.sock", "/x/y.sock.lock"])
        env.pop("CODESRV_SOCK")
        r = subprocess.run(
            [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(HERE.parent)!r}); {code}"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(r.stdout.split(), [f"/tmp/codesrv-{os.getuid()}.sock", f"/tmp/codesrv-{os.getuid()}.lock"])

    def test_usage_log_nav_op(self):
        log = os.path.join(self.tmp, "u.jsonl")
        self.serve({"out": "", "err": "", "code": 0})
        with mock.patch.dict(os.environ, {"LP_USAGE_LOG": log}), mock.patch.object(local, "SOCK", self.sock), contextlib.redirect_stdout(io.StringIO()):
            local.run_logged(["nav", "def", "a.py", "1"])
        self.assertEqual(json.loads(Path(log).read_text())["cmd"], "nav def")


if __name__ == "__main__":
    unittest.main()
