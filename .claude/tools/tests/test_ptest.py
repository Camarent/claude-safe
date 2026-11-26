"""ptest.py end to end on a scratch tests dir: pass, fail, skip count, CI=1 fails on skips."""

import contextlib
import io
import os
import subprocess
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

    def test_repo_path_pattern_selects_by_file_name(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_a.py"), "w", encoding="utf-8") as f:
                f.write(OK)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ptest.main(["--dir", d, "--jobs", "2", ".claude/tools/tests/test_a.py"])
        self.assertEqual(rc, 0)
        self.assertIn("1 files", buf.getvalue())


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


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


def make_repo(repo):
    git(repo, "init", "-q", "-b", "master")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")


class BranchStateTest(unittest.TestCase):
    def setUp(self):
        repo = tempfile.TemporaryDirectory()  # a scratch main checkout, never the real one
        self.addCleanup(repo.cleanup)
        self.repo = repo.name
        make_repo(self.repo)
        patcher = mock.patch.object(ptest, "HERE", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_state_is_current_branch_and_sorted_branch_list(self):
        git(self.repo, "branch", "stage/b")
        git(self.repo, "branch", "stage/a")
        self.assertEqual(ptest.main_branch_state(), ("master", ["master", "stage/a", "stage/b"]))

    def test_no_git_gives_empty_state(self):
        with mock.patch.object(ptest.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertEqual(ptest.main_branch_state(), ("", []))

    def test_unchanged_state_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            moved = ptest.warn_branch_changes(("master", ["master"]), ("master", ["master"]))
        self.assertFalse(moved)
        self.assertEqual(buf.getvalue(), "")

    def test_switched_branch_is_named_with_new_branches(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            moved = ptest.warn_branch_changes(("master", ["master"]), ("stage/x", ["master", "stage/x"]))
        self.assertTrue(moved)
        self.assertIn("master -> stage/x", buf.getvalue())
        self.assertIn("new branches: stage/x", buf.getvalue())

    def test_run_where_a_test_switches_branch_fails(self):
        # the fake test file switches the scratch main checkout to a new branch: the run must exit non-zero
        body = (
            "import subprocess, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            f"        subprocess.run(['git', '-C', {self.repo!r}, 'checkout', '-q', '-b', 'stage/leak'], check=True)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_switch.py"), "w", encoding="utf-8") as f:
                f.write(body)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {ptest.ENV_LOG_DIR: d}), contextlib.redirect_stdout(buf):
                rc = ptest.main(["--dir", d, "--jobs", "1"])
        self.assertEqual(rc, 1)
        self.assertIn("master -> stage/leak", buf.getvalue())
        self.assertEqual(ptest.main_branch_state()[0], "stage/leak")

    def test_run_with_no_branch_change_passes(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {ptest.ENV_LOG_DIR: d}), contextlib.redirect_stdout(buf):
            with open(os.path.join(d, "test_ok.py"), "w", encoding="utf-8") as f:
                f.write(OK)
            rc = ptest.main(["--dir", d, "--jobs", "1"])
        self.assertEqual(rc, 0)
        self.assertNotIn("ERROR", buf.getvalue())


class SelectFilesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tests = os.path.join(tmp.name, "tests")
        os.mkdir(self.tests)
        for name in ("test_a.py", "test_b.py"):
            with open(os.path.join(self.tests, name), "w", encoding="utf-8") as f:
                f.write(OK)
        cwd = os.getcwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, cwd)

    def test_bare_name_and_glob_select_by_basename(self):
        self.assertEqual(ptest.select_files(self.tests, ["test_a.py"]), ["test_a.py"])
        self.assertEqual(sorted(ptest.select_files(self.tests, ["test_*.py"])), ["test_a.py", "test_b.py"])

    def test_repo_path_to_file_in_tests_dir_selects_it(self):
        self.assertEqual(ptest.select_files(self.tests, [os.path.join("tests", "test_b.py")]), ["test_b.py"])

    def test_path_outside_tests_dir_selects_nothing(self):
        with open("other.py", "w", encoding="utf-8") as f:
            f.write(OK)
        self.assertEqual(ptest.select_files(self.tests, ["other.py"]), [])


if __name__ == "__main__":
    unittest.main()
