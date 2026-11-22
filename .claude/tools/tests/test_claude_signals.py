"""pipeline/claude.py: stop signals (SIGHUP included, PID 1 excepted), a stopped session is no answer, and the stage pgid file (fake `claude` on a temp PATH, no model)."""

import os
import signal
import stat
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, driver
from pipeline.configuration import ENV_SCRATCH_DIR, PGID_FILE_NAME
from tests.support.tmpdirs import tracked_mkdtemp

OK_BODY = 'printf \'{"result":"ok"}\''


class FakeClaude(unittest.TestCase):
    def setUp(self):
        self.bin = tracked_mkdtemp()
        self.cwd = tracked_mkdtemp()
        patcher = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("POLL_S", 0.1), ("REPORT_GRACE_S", 0.2)):
            p = mock.patch.object(claude, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fake(self, body):
        path = os.path.join(self.bin, "claude")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class StopSignalTest(unittest.TestCase):
    def test_sighup_is_a_stop_signal_next_to_term_and_int(self):
        before = signal.getsignal(signal.SIGHUP)
        with claude.kill_on_signal():
            self.assertNotEqual(signal.getsignal(signal.SIGHUP), before)
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
        self.assertEqual(signal.getsignal(signal.SIGHUP), before)  # restored on exit

    def test_pid_one_leaves_sighup_alone(self):
        before = signal.getsignal(signal.SIGHUP)
        with mock.patch.object(claude.os, "getpid", return_value=claude.INIT_PID), claude.kill_on_signal():
            self.assertEqual(signal.getsignal(signal.SIGHUP), before)
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), before)


class StoppedSessionTest(FakeClaude):
    def test_no_session_starts_while_stopping(self):
        self.fake(OK_BODY)
        with mock.patch.object(claude, "STOPPING", True):
            res = claude.run_claude(["-p", "x"], self.cwd, 5)
        self.assertTrue(res["stopped"])
        self.assertTrue(claude.usage(res)["stopped"])

    def test_session_killed_by_stop_is_stopped_not_an_answer(self):
        self.fake(OK_BODY)

        def stop_during_wait(p, timeout, done_file):
            claude.STOPPING = True  # the stop handler ran while the session was live
            return None, None, False, False

        with mock.patch.object(claude, "_wait", side_effect=stop_during_wait):
            try:
                res = claude.run_claude(["-p", "x"], self.cwd, 5)
            finally:
                claude.STOPPING = False
        self.assertTrue(res["stopped"])


class StoppedDispatchTest(unittest.TestCase):
    def test_stopped_dispatch_writes_no_report(self):
        stage = tracked_mkdtemp()
        c = types.SimpleNamespace(w=stage, dir=stage, stage="s", tasks=dict)
        d = {"id": "a1", "agent": "dev", "model": "m", "prompt": "p", "effort": None}
        seen = {}

        def stopped_session(args, cwd, done_file=None, env_extra=None, pgid_file=None):
            seen["pgid_file"] = pgid_file
            return claude.usage({"stopped": True, "is_error": True, "result": "wave: driver stopping"})

        with (
            mock.patch.object(driver.claude, "session", stopped_session),
            mock.patch.object(driver.claude, "claude_args", lambda *a, **k: ["-p", "x"]),
            mock.patch.object(driver, "scratch_env", lambda c: {ENV_SCRATCH_DIR: stage}),
            mock.patch.object(driver, "orch", lambda *a, **k: {}),
        ):
            _, u, rep = driver.run_one(c, d)
        self.assertTrue(u["stopped"])
        self.assertEqual(rep, {})
        self.assertEqual(seen["pgid_file"], os.path.join(stage, PGID_FILE_NAME))
        self.assertFalse(os.path.exists(os.path.join(stage, "reports", "report-a1.md")))
        self.assertFalse(os.path.exists(os.path.join(stage, "report-a1.md")))


class PgidFileTest(FakeClaude):
    def test_live_session_pgid_is_in_the_file_and_the_file_goes_at_the_end(self):
        self.fake(OK_BODY)
        path = os.path.join(self.cwd, "wave.pgid")
        seen = []
        real_record = claude.pgid_record

        def spy(p, pid):
            real_record(p, pid)
            with open(p, encoding="utf-8") as f:
                seen.append(f.read())

        with mock.patch.object(claude, "pgid_record", side_effect=spy):
            res = claude.run_claude(["-p", "x"], self.cwd, 5, pgid_file=path)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].strip().isdigit())
        self.assertFalse(os.path.exists(path))

    def test_forget_keeps_the_other_live_sessions(self):
        path = os.path.join(tracked_mkdtemp(), "wave.pgid")
        claude.pgid_record(path, 101)
        claude.pgid_record(path, 202)
        claude.pgid_forget(path, 101)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "202\n")
        claude.pgid_forget(path, 202)
        self.assertFalse(os.path.exists(path))
