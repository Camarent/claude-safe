"""Script steps (no model): a timeout, a broken command or a missing cwd is an error result, and paths with a space reach the tool intact."""

import os
import shlex
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, driver, util
from pipeline.configuration import ENV_SCRIPT_TIMEOUT, PROJECT_DEFAULTS, SCRIPT_TIMEOUT_S
from tests.support.tmpdirs import tracked_mkdtemp


class RunScriptTest(unittest.TestCase):
    def test_a_script_past_its_timeout_is_an_error_result(self):
        # the env wins over the default: a parent run with ORCH_SCRIPT_TIMEOUT set (acceptance qrun) must not lift the 0.001 s limit
        with mock.patch.object(util, "SCRIPT_TIMEOUT_S", 0.001), mock.patch.dict(os.environ, {ENV_SCRIPT_TIMEOUT: "0.001"}):
            r = driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", ".")
        self.assertTrue(r["error"])
        self.assertIn("timed out", r["result"])
        self.assertTrue(r["result"].startswith("VERDICT: FAIL"))

    def test_timeout_kills_grandchildren_too(self):  # review audit M3
        with tempfile.TemporaryDirectory() as td:
            pidf = os.path.join(td, "pid")
            with open(os.path.join(td, "gc.py"), "w", encoding="utf-8") as f:
                f.write(f"import subprocess, sys, time\np = subprocess.Popen(['sleep', '60'])\nopen({pidf!r}, 'w').write(str(p.pid))\ntime.sleep(60)\n")
            with mock.patch.object(driver, "SCRIPT_PREFIX", f"python3 -I {td}/"):
                r = driver.run_script(f"{driver.SCRIPT_PREFIX}gc.py", td, timeout=2)
            self.assertIn("timed out", r["result"])
            pid = int(open(pidf, encoding="utf-8").read())
            self.addCleanup(lambda: os.path.exists(f"/proc/{pid}") and os.kill(pid, 9))
            end = time.monotonic() + 10  # SIGKILL lands a moment after run_script returns: wait until the grandchild is gone or a zombie
            alive = True
            while alive and time.monotonic() < end:
                alive = os.path.exists(f"/proc/{pid}") and "Z" not in open(f"/proc/{pid}/stat", encoding="utf-8").read().split(")")[-1][:3]
                if alive:
                    time.sleep(0.02)
            self.assertFalse(alive)

    def test_interrupt_kills_the_script_group(self):  # review2 fixes (6)
        proc = mock.MagicMock()
        proc.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with (
            mock.patch.object(driver.subprocess, "Popen", return_value=proc),
            mock.patch.object(driver.claude, "kill_group") as kg,
            self.assertRaises(KeyboardInterrupt),
        ):
            driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", ".")
        kg.assert_called_once_with(proc)

    def test_non_positive_or_nan_env_timeout_is_ignored(self):
        for bad in ("0", "-5", "nan"):
            with mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": bad}):
                self.assertEqual(driver.script_timeout(), SCRIPT_TIMEOUT_S)

    def test_env_script_timeout_overrides_the_default(self):
        with mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": "0.001"}):
            r = driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", ".")
        self.assertTrue(r["error"])
        self.assertIn("timed out", r["result"])

    def test_a_step_timeout_argument_beats_the_env_and_the_default(self):
        with mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": "1800"}):
            r = driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", ".", timeout=0.001)
        self.assertIn("timed out", r["result"])

    def test_a_bad_env_script_timeout_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {"ORCH_SCRIPT_TIMEOUT": "soon"}):
            self.assertEqual(driver.script_timeout(), SCRIPT_TIMEOUT_S)

    def test_an_unparsable_command_is_an_error_result_not_a_crash(self):
        r = driver.run_script(f"{driver.SCRIPT_PREFIX}x.py 'unclosed", ".")
        self.assertTrue(r["error"])
        self.assertTrue(r["result"].startswith("VERDICT: FAIL"))

    def test_a_missing_cwd_is_an_error_result_not_a_crash(self):
        d = tracked_mkdtemp()
        r = driver.run_script(f"{driver.SCRIPT_PREFIX}qrun.py", os.path.join(d, "gone"))
        self.assertTrue(r["error"])
        self.assertTrue(r["result"].startswith("VERDICT: FAIL"))


class PathQuotingTest(unittest.TestCase):
    def test_audit_tool_steps_keep_a_path_with_a_space_as_one_argument(self):
        d = os.path.join(tracked_mkdtemp(), "a b")
        audit_dir = os.path.join(d, "audit")
        a = SimpleNamespace(stage="s", dir=d)
        cfg = dict(PROJECT_DEFAULTS, audit_file="AUDIT.md")
        steps = [s for group in audit_phases._audit_tool_phases(a, cfg, {"ui": False}, audit_dir, True) for s in group]
        run = next(s for s in steps if s["id"] == "audit-run")
        self.assertIn(audit_dir, shlex.split(run["prompt"]))  # one argv item, not split at the space
        self.assertIn(audit_dir, shlex.split(next(s for s in steps if s["id"] == "tools-check")["prompt"]))

    def test_audit_run_gets_the_stage_base_for_the_suppression_check(self):
        d = tracked_mkdtemp()
        a = SimpleNamespace(stage="s", dir=d)
        cfg = dict(PROJECT_DEFAULTS, audit_file="AUDIT.md", branch_release="rel")
        steps = [s for group in audit_phases._audit_tool_phases(a, cfg, {"ui": False}, d, True) for s in group]
        argv = shlex.split(next(s for s in steps if s["id"] == "audit-run")["prompt"])
        self.assertEqual(argv[argv.index("--base") + 1], "rel")


if __name__ == "__main__":
    unittest.main()
