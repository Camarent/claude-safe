"""Driver robustness: signal stop of live sessions, gh probe that cannot run, quoted script dirs, inf script timeout, bad orch output, a crashing dispatch."""

import os
import shlex
import signal
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, driver, pr_mode, util
from pipeline.configuration import CHECKS_TIMEOUT_S, EV_DISPATCH, MODEL_SONNET, VERDICT_PREFIX
from pipeline.ctx import Ctx
from tests.support.tmpdirs import tracked_mkdtemp


class KillOnSignal(unittest.TestCase):
    def test_sigterm_kills_tracked_session_group(self):
        p = claude.track(subprocess.Popen(["sleep", "60"], start_new_session=True))
        self.addCleanup(lambda: (claude.untrack(p), claude.kill_group(p), p.wait()))
        with self.assertRaises(SystemExit), claude.kill_on_signal():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)  # the handler raises at once; not reached
        self.assertEqual(p.wait(timeout=5), -signal.SIGKILL)

    def test_stop_flag_refuses_new_sessions(self):
        with mock.patch.object(claude, "STOPPING", True):
            res = claude.run_claude(["-p"], os.getcwd(), 5)
        self.assertTrue(res["is_error"])
        self.assertIn("stopping", res["result"])


class GhProbe(unittest.TestCase):
    def setUp(self):
        self.dir = tracked_mkdtemp()
        self.a = SimpleNamespace(dir=self.dir, stage="s", feature="f", dry_run=False)
        for name, value in (("repo_path", self.dir), ("has_origin", True), ("github_origin", True), ("shutil.which", "/usr/bin/gh")):
            p = mock.patch.object(pr_mode, name, return_value=value) if "." not in name else mock.patch.object(pr_mode.shutil, "which", return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(pr_mode._GH_OK.discard, self.dir)

    def probe(self, *results):
        p = mock.patch.object(pr_mode, "_run", side_effect=list(results))
        p.start()
        self.addCleanup(p.stop)

    def test_probe_that_cannot_run_is_not_down(self):
        self.probe(None)
        self.assertFalse(pr_mode.gh_down(self.a))

    def test_explicit_auth_failure_is_down(self):
        self.probe(SimpleNamespace(returncode=1))
        self.assertTrue(pr_mode.gh_down(self.a))

    def test_pass_is_cached_across_a_blip(self):
        self.probe(SimpleNamespace(returncode=0), None, SimpleNamespace(returncode=1))
        self.assertTrue(pr_mode.pr_active(self.a))
        self.assertTrue(pr_mode.pr_active(self.a))  # probe could not run: still green
        self.assertFalse(pr_mode.gh_down(self.a))


class QuotedDir(unittest.TestCase):
    def test_pr_step_command_keeps_dir_with_space(self):
        d = tracked_mkdtemp() + "/my proj"
        a = SimpleNamespace(dir=d, stage="s", feature="f")
        argv = shlex.split(pr_mode.cmd(a, "x", "open"))
        self.assertEqual(argv[argv.index("--dir") + 1], os.path.abspath(d))


class ScriptLimits(unittest.TestCase):
    def test_inf_env_gives_default(self):
        with mock.patch.dict(os.environ, {util.ENV_SCRIPT_TIMEOUT: "inf"}), mock.patch.object(util, "tool_setting", return_value=42.0):
            self.assertEqual(util.script_timeout(), 42.0)

    def test_step_limit_exceeds_pr_checks_wait(self):
        with mock.patch.dict(os.environ, {util.ENV_SCRIPT_TIMEOUT: "60"}):
            self.assertGreater(driver.script_step_limit({}), CHECKS_TIMEOUT_S)
            self.assertEqual(driver.script_step_limit({"pr_checks_timeout": 3000}), 3060)

    def test_inf_script_timeout_is_an_error_result_not_a_crash(self):
        with (
            mock.patch.dict(os.environ, {util.ENV_SCRIPT_TIMEOUT: "inf"}),
            mock.patch.object(driver.subprocess, "Popen", side_effect=OverflowError("inf")),
        ):
            res = driver.run_script(f"{driver.SCRIPT_PREFIX}x.py", os.getcwd(), timeout=float("inf"))
        self.assertTrue(res["error"])


class OrchOutput(unittest.TestCase):
    def test_non_json_last_line_asks_lead(self):
        r = SimpleNamespace(returncode=0, stdout="warning text\n", stderr="")
        with mock.patch.object(driver, "run_py", return_value=r):
            res = driver.orch("next", "s", ".")
        self.assertEqual(res["action"], "ask_main")


class RunOneCrash(unittest.TestCase):
    def test_exception_becomes_blocked_result_and_reports(self):
        w = tracked_mkdtemp()
        c = SimpleNamespace(w=w, stage="s", dir=w)
        d = {"id": "a1", "agent": "dev", "model": "m", "prompt": "p"}
        with (
            mock.patch.object(driver, "run_one", side_effect=RuntimeError("boom")),
            mock.patch.object(driver, "orch", return_value={"verdict": "BLOCKED"}) as rep,
        ):
            got_d, u, _res = driver.safe_run_one(c, d)
        self.assertIs(got_d, d)
        self.assertTrue(u["error"])
        self.assertTrue(u["result"].startswith(VERDICT_PREFIX + "BLOCKED"))
        self.assertIn("boom", u["result"])
        rep.assert_called_once_with("report", "s", w, "--id", "a1")


class CtxRestore(unittest.TestCase):
    def test_dispatch_counters_survive_a_restart(self):
        d = tracked_mkdtemp()
        c = Ctx(SimpleNamespace(stage="s", dir=d))
        os.makedirs(os.path.dirname(c.log), exist_ok=True)
        util.append_event(c.log, {"ev": EV_DISPATCH, "id": "a1", "agent": "dev", "model": MODEL_SONNET})
        util.append_event(c.log, {"ev": EV_DISPATCH, "id": "s1", "agent": "script", "model": ""})
        again = Ctx(SimpleNamespace(stage="s", dir=d))
        self.assertEqual(again.disp, 1)
        self.assertEqual(again.sessions, 1)


if __name__ == "__main__":
    unittest.main()
