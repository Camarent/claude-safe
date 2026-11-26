"""ptest fails the run and names the test file when a test switches the main checkout's branch; a clean run stays green."""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ptest
from tests.support.tmpdirs import tracked_tmpdir

GIT_ID = ["-c", "user.email=ptest@example.invalid", "-c", "user.name=ptest"]


def git(repo, *args):
    subprocess.run(["git", *GIT_ID, *args], cwd=repo, check=True, capture_output=True)


def fixture_repo(test):
    """A scratch git repo on master with one commit; it stands in for the main checkout."""
    repo = tracked_tmpdir(prefix="ptest-guard-repo-")
    test.addCleanup(repo.cleanup)
    git(repo.name, "init", "-q", "-b", "master")
    git(repo.name, "commit", "-q", "--allow-empty", "-m", "init")
    return repo.name


SWITCH_LEAK = ["switch", "-q", "-c", "leak"]


def write_tests(test, repo, leak_args=None):
    """A scratch tests dir with one test file; a leaking one runs `git <leak_args>` in the fixture repo (switch to a new branch by default in the tests)."""
    tests = tracked_tmpdir(prefix="ptest-guard-tests-")
    test.addCleanup(tests.cleanup)
    body = (
        "import subprocess\nimport unittest\n\n\nclass T(unittest.TestCase):\n    def test_branch(self):\n"
        f"        subprocess.run({['git', *leak_args]!r}, cwd={repo!r}, check=True)\n"
        if leak_args
        else "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_clean(self):\n        pass\n"
    )
    with open(os.path.join(tests.name, "test_switch.py"), "w", encoding="utf-8") as f:
        f.write(body)
    return tests.name


class BranchGuard(unittest.TestCase):
    def run_ptest(self, repo, tests):
        out = io.StringIO()
        with mock.patch.object(ptest, "HERE", repo), contextlib.redirect_stdout(out):
            rc = ptest.main(["--jobs", "1", "--dir", tests, "test_switch.py"])
        return rc, out.getvalue()

    def test_switching_test_fails_and_names_file(self):
        repo = fixture_repo(self)
        rc, out = self.run_ptest(repo, write_tests(self, repo, SWITCH_LEAK))
        self.assertEqual(rc, 1, out)
        self.assertIn("main checkout branch changed during this run", out)
        self.assertIn("seen during test files: test_switch.py", out)

    def test_clean_run_stays_green(self):
        repo = fixture_repo(self)
        rc, out = self.run_ptest(repo, write_tests(self, repo))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("branch changed", out)

    def test_job_branch_created_mid_run_stays_quiet(self):
        repo = fixture_repo(self)
        rc, out = self.run_ptest(repo, write_tests(self, repo, ["branch", "job/x"]))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("branch changed", out)

    def test_stage_branch_created_mid_run_warns(self):
        repo = fixture_repo(self)
        rc, out = self.run_ptest(repo, write_tests(self, repo, ["branch", "stage/x"]))
        self.assertEqual(rc, 1, out)
        self.assertIn("main checkout branch changed during this run", out)
        self.assertIn("new branches: stage/x", out)


if __name__ == "__main__":
    unittest.main()
