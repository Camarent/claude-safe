"""plan.py testrun: judge table with a fake runner, and testrun_stage on a throwaway git repo (no real test suite runs)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_testrun

# (name, exit code, output, rejected?)
CASES = [
    ("pass", 0, "1 passed", False),
    ("assertion failure", 1, "AssertionError: 2 != 3", False),
    ("import error", 1, "ImportError: cannot import name 'x'", False),
    ("module not found", 1, "ModuleNotFoundError: No module named 'y'", False),
    ("attribute error", 1, "AttributeError: module has no attribute 'z'", False),
    ("collection error of a new file", 2, "ERROR collecting tests/test_new.py", False),
    ("no match, exit 3", 3, "", True),
    ("no match, message", 5, "ERROR: no test files match the pattern", True),
    ("command not found, exit 127", 127, "sh: 1: pytestx: not found", True),
    ("not executable, exit 126", 126, "sh: 1: ./run: Permission denied", True),
    ("command not found message", 2, "sh: 1: pytestx: command not found", True),
    ("missing executable", 2, "sh: 1: pytest: No such file or directory", True),
]


class Judge(unittest.TestCase):
    def test_table(self):
        # the rejected rows must produce a problem: an always-accept judge fails here
        for name, code, out, rejected in CASES:
            with self.subTest(name):
                msg = plan_testrun.judge("task-1", "pytest -q", code, out)
                if rejected:
                    self.assertIn("task-1", msg)
                else:
                    self.assertEqual(msg, "")


class Stage(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-testrun-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", self.root, *a], check=True, capture_output=True)
        with open(os.path.join(self.root, "f.txt"), "w") as f:
            f.write("x\n")
        for a in (["add", "f.txt"], ["commit", "-qm", "c"]):
            subprocess.run(["git", "-C", self.root, *a], check=True, capture_output=True)
        with open(os.path.join(self.d, "TASKS.md"), "w") as f:
            f.write(
                "## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n- [ ] task-2 two - owner: haiku - review: wave W1, haiku\n\n## Graph\nwave 1: task-1, task-2\n"
            )

    def task(self, tid, test, kind="code"):
        with open(os.path.join(self.d, tid + ".md"), "w") as f:
            f.write(f"Type: {kind}\nPredicted paths: f.txt\nTest: {test}\n")

    def fake(self, results):
        calls = []

        def run(wt, cmd):
            calls.append(cmd)
            # the worktree is a real checkout of HEAD: the file the repo has must be there
            self.assertTrue(os.path.exists(os.path.join(wt, "f.txt")))
            return results[cmd]

        return run, calls

    def test_rejects_no_match_and_keeps_accepted(self):
        self.task("task-1", "pytest -q tests/test_gone.py")
        self.task("task-2", "pytest -q tests/test_new.py")
        run, calls = self.fake({"pytest -q tests/test_gone.py": (3, ""), "pytest -q tests/test_new.py": (1, "AssertionError")})
        n, probs = plan_testrun.testrun_stage(self.d, self.root, runner=run)
        self.assertEqual(n, 2)
        self.assertEqual(calls, ["pytest -q tests/test_gone.py", "pytest -q tests/test_new.py"])
        self.assertEqual(len(probs), 1)
        self.assertIn("task-1", probs[0])

    def test_skips_webtest_and_report(self):
        self.task("task-1", "pytest -q x", kind="webtest")
        self.task("task-2", "pytest -q y", kind="report")
        run, calls = self.fake({})
        self.assertEqual(plan_testrun.testrun_stage(self.d, self.root, runner=run), (0, []))
        self.assertEqual(calls, [])

    def test_cleans_up_worktree(self):
        self.task("task-1", "pytest -q x")
        self.task("task-2", "pytest -q y")
        run, _ = self.fake({"pytest -q x": (0, "1 passed"), "pytest -q y": (0, "1 passed")})
        plan_testrun.testrun_stage(self.d, self.root, runner=run)
        wl = subprocess.run(["git", "-C", self.root, "worktree", "list"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(len(wl.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
