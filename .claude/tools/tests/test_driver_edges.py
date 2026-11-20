"""Edges not covered elsewhere: driver.run_script refusal and output cut, driver.orch failure."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import driver
from pipeline.configuration import SCRIPT_OUTPUT_MAX


class TestDriver(unittest.TestCase):
    def test_run_script_refuses_outside_tools(self):
        r = driver.run_script("rm -rf /", ".")
        self.assertTrue(r["error"])
        self.assertIn("refused script", r["result"])

    def test_run_script_cuts_long_output_but_keeps_verdict_line(self):
        cmd = f"{driver.SCRIPT_PREFIX}x.py"
        out = "VERDICT: DONE\n" + "z" * (SCRIPT_OUTPUT_MAX * 2)
        done = SimpleNamespace(returncode=0, args=[], communicate=lambda timeout=None: (out, ""))
        with mock.patch.object(driver.subprocess, "Popen", return_value=done):
            r = driver.run_script(cmd, ".")
        self.assertFalse(r["error"])
        self.assertTrue(r["result"].startswith("VERDICT: DONE\n...\n"))
        self.assertLessEqual(len(r["result"]), SCRIPT_OUTPUT_MAX + 30)

    def test_orch_failure_asks_main(self):
        bad = SimpleNamespace(returncode=2, stdout="", stderr="boom")
        with mock.patch.object(driver, "run_py", return_value=bad):
            r = driver.orch("next", "s", ".")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("boom", r["message"])

    def test_orch_inproc_exit_and_json_line(self):
        from pipeline import cli_orch

        def fail(argv):
            print("x")
            raise SystemExit(2)

        def ok(argv):
            print('{"action": "done"}')

        with mock.patch.object(cli_orch, "main", fail):
            self.assertEqual(driver.orch_inproc("next", "s", ".")["action"], "ask_main")
        with mock.patch.object(cli_orch, "main", ok):
            self.assertEqual(driver.orch_inproc("next", "s", "."), {"action": "done"})

    def test_orch_inproc_other_exception_and_exit_text(self):  # review audit M1
        from pipeline import cli_orch

        def boom(argv):
            raise RuntimeError("git hung")

        def refuse(argv):
            raise SystemExit("orch: stage plan found in x")

        with mock.patch.object(cli_orch, "main", boom):
            r = driver.orch_inproc("next", "s", ".")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("git hung", r["message"])
        with mock.patch.object(cli_orch, "main", refuse):
            r = driver.orch_inproc("next", "s", ".")
        self.assertIn("stage plan found in x", r["message"])


if __name__ == "__main__":
    unittest.main()
