"""ptest guard: a pattern that matches no file fails the run; a run from a tree imports that tree's pipeline code."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import ptest  # noqa: E402  (import after the sys.path insert)

OK = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"
MUTATION = "\nraise SystemExit('mutated pipeline: this tree is the one ptest imports')\n"


class NoMatchTest(unittest.TestCase):
    def run_main(self, patterns):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_a.py"), "w", encoding="utf-8") as f:
                f.write(OK)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ptest.main(["--dir", d, "--jobs", "2", *patterns])
        return rc, buf.getvalue()

    def test_pattern_with_no_file_fails(self):
        rc, out = self.run_main(["test_none.py"])
        self.assertEqual(rc, 1)
        self.assertIn("no test files match: test_none.py", out)

    def test_pattern_with_a_file_passes(self):
        rc, _ = self.run_main(["test_a.py"])
        self.assertEqual(rc, 0)

    def test_missing_named_file_fails_beside_a_matching_one(self):
        rc, out = self.run_main(["test_a.py", "test_none.py"])
        self.assertEqual(rc, 1)
        self.assertIn("no test files match: test_none.py", out)


class ImportTreeTest(unittest.TestCase):
    def make_tree(self, mutated):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tools = os.path.join(tmp.name, "tools")
        os.makedirs(os.path.join(tools, "tests"))
        shutil.copy(ptest.__file__, tools)
        shutil.copytree(os.path.join(TOOLS, "pipeline"), os.path.join(tools, "pipeline"), ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(tools, "tests", "test_ok.py"), "w", encoding="utf-8") as f:
            f.write(OK)
        if mutated:
            with open(os.path.join(tools, "pipeline", "configuration", "__init__.py"), "a", encoding="utf-8") as f:
                f.write(MUTATION)
        return tools

    def run_tree(self, tools):
        return subprocess.run(
            [sys.executable, "-I", os.path.join(tools, "ptest.py"), "--dir", os.path.join(tools, "tests"), "--jobs", "1", "test_ok.py"],
            capture_output=True,
            text=True,
            check=False,
            cwd=os.path.dirname(tools),
        )

    def test_pristine_tree_passes(self):
        self.assertEqual(self.run_tree(self.make_tree(mutated=False)).returncode, 0)

    def test_mutated_pipeline_in_tree_fails_the_run(self):
        # the real checkout's pipeline is untouched, so only the tree's own copy can fail this run
        res = self.run_tree(self.make_tree(mutated=True))
        self.assertEqual(res.returncode, 1)
        self.assertIn("mutated pipeline", res.stderr)
