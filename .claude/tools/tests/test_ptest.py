"""ptest.py end to end on a scratch tests dir: pass, fail, skip count, CI=1 fails on skips."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ptest

OK = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"
SKIP = "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('x')\n    def test_a(self):\n        pass\n"
RED = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.fail('boom')\n"


class PtestTest(unittest.TestCase):
    def setUp(self):
        logs = tempfile.TemporaryDirectory()  # failing-file logs go to a temp dir, never into the checkout
        self.addCleanup(logs.cleanup)
        patcher = mock.patch.dict(os.environ, {ptest.ENV_LOG_DIR: logs.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_ptest(self, files, env=None):
        with tempfile.TemporaryDirectory() as d:
            for name, body in files.items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(body)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(buf):
                os.environ.pop("CI", None) if not env else None
                rc = ptest.main(["--dir", d, "--jobs", "2"])
        return rc, buf.getvalue()

    def test_pass_and_skip_count(self):
        rc, out = self.run_ptest({"test_a.py": OK, "test_b.py": SKIP})
        self.assertEqual(rc, 0)
        self.assertIn("2 files, 0 failed, 1 tests skipped", out)

    def test_failing_file_is_named(self):
        rc, out = self.run_ptest({"test_a.py": OK, "test_r.py": RED})
        self.assertEqual(rc, 1)
        self.assertIn("FAIL test_r.py", out)
        self.assertIn("1 failed", out)

    def test_failing_file_shows_its_output(self):
        rc, out = self.run_ptest({"test_r.py": RED})
        self.assertEqual(rc, 1)
        self.assertIn("AssertionError: boom", out)
        self.assertIn("test_a (test_r.T", out)

    def test_skips_fail_the_run_under_ci(self):
        rc, out = self.run_ptest({"test_b.py": SKIP}, env={"CI": "1"})
        self.assertEqual(rc, 1)
        self.assertIn("skipped tests fail the run", out)

    def test_summary_names_failing_files_and_keeps_log(self):
        with tempfile.TemporaryDirectory() as logs:
            rc, out = self.run_ptest({"test_a.py": OK, "test_r.py": RED}, env={ptest.ENV_LOG_DIR: logs})
            summary = out.strip().splitlines()[-1]
            self.assertEqual(rc, 1)
            self.assertTrue(summary.startswith("FAILED test_r.py: 2 files, 1 failed"), summary)
            with open(os.path.join(logs, "test_r.py.log"), encoding="utf-8") as f:
                self.assertIn("AssertionError: boom", f.read())
            self.assertFalse(os.path.exists(os.path.join(logs, "test_a.py.log")))

    def test_pass_has_no_failed_prefix(self):
        with tempfile.TemporaryDirectory() as logs:
            rc, out = self.run_ptest({"test_a.py": OK}, env={ptest.ENV_LOG_DIR: logs})
            self.assertEqual(rc, 0)
            self.assertNotIn("FAILED", out)
            self.assertEqual(os.listdir(logs), [])

    def test_unwritable_log_dir_does_not_fail_the_run(self):
        with tempfile.NamedTemporaryFile() as blocker:  # a file where the log dir should be: makedirs raises
            rc, out = self.run_ptest({"test_r.py": RED}, env={ptest.ENV_LOG_DIR: os.path.join(blocker.name, "sub")})
        self.assertEqual(rc, 1)
        self.assertIn("not saved", out)

    def test_no_files_match(self):
        rc, out = self.run_ptest({})
        self.assertEqual(rc, 1)
        self.assertIn("no test files match", out)


class ClaudeChangeWarningTest(unittest.TestCase):
    def test_new_change_is_warned_and_returned(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            new = ptest.warn_new_claude_changes({" M .claude/a.md"}, {" M .claude/a.md", "?? .claude/b.py"})
        self.assertEqual(new, ["?? .claude/b.py"])
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("?? .claude/b.py", buf.getvalue())

    def test_no_new_change_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            new = ptest.warn_new_claude_changes({" M .claude/a.md"}, {" M .claude/a.md"})
        self.assertEqual(new, [])
        self.assertEqual(buf.getvalue(), "")

    def test_no_git_gives_empty_status(self):
        with mock.patch.object(ptest.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertEqual(ptest.main_claude_status(), set())

    def test_run_warns_but_keeps_exit_code(self):
        statuses = iter([set(), {"?? .claude/x.py"}])
        with mock.patch.object(ptest, "main_claude_status", side_effect=lambda: next(statuses)), tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_ok.py"), "w", encoding="utf-8") as f:
                f.write(OK)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {ptest.ENV_LOG_DIR: d}), contextlib.redirect_stdout(buf):
                rc = ptest.main(["--dir", d, "--jobs", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("WARNING", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
