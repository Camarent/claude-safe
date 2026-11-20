"""A test run leaves nothing behind in the temp dir: run tests as a child with its own TMPDIR and list what stays."""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_FILES = ["test_ledger.py", "test_wt.py", "test_commit.py", "test_util_jsonl.py"]  # mkdtemp in setUp, in the test, TemporaryDirectory
DECOY = """
import tempfile
import unittest


class Decoy(unittest.TestCase):
    def test_leaks(self):
        tempfile.mkdtemp()
        tempfile.mkstemp()
"""
TRACKED = """
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp, tracked_tmpdir


class Tracked(unittest.TestCase):
    def test_cleans(self):
        tracked_mkdtemp()
        tracked_tmpdir()
"""


def leftovers(pattern, cwd):
    """Run unittest discovery of files matching `pattern` under cwd (as ptest.py does) with a fresh TMPDIR; -> names left in that TMPDIR."""
    scratch = tempfile.mkdtemp()
    try:
        env = dict(os.environ, TMPDIR=scratch)
        cmd = [sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", pattern]
        subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, check=False)
        return sorted(os.listdir(scratch))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


class TmpLeakTest(unittest.TestCase):
    def module_dir(self, name, code):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
        with open(os.path.join(d, name + ".py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(code))
        return d

    def test_decoy_leak_is_detected(self):
        d = self.module_dir("decoy_leak", DECOY)
        self.assertEqual(len(leftovers("decoy_leak.py", cwd=d)), 2)

    def test_tracked_helpers_leave_nothing(self):
        d = self.module_dir("tests/tracked_ok", TRACKED)
        shutil.copytree(os.path.join(TOOLS, "tests", "support"), os.path.join(d, "tests", "support"), ignore=shutil.ignore_patterns("__pycache__"))
        self.assertEqual(leftovers("tracked_ok.py", cwd=os.path.join(d, "tests")), [])

    def test_sample_of_suite_leaves_nothing(self):
        for name in SAMPLE_FILES:
            with self.subTest(name):
                self.assertEqual(leftovers(name, cwd=os.path.join(TOOLS, "tests")), [])


if __name__ == "__main__":
    unittest.main()
