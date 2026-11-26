"""gitx: out raises on failure, try_out gives None, run gives the process; a timeout is named."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx


class GitxTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        subprocess.run(["git", "init", "-q", self.d], check=True)

    def test_out_returns_stdout_without_trailing_newline(self):
        self.assertEqual(gitx.out(self.d, "rev-parse", "--is-inside-work-tree"), "true")

    def test_out_raises_on_failure(self):
        with self.assertRaisesRegex(RuntimeError, "rev-parse"):
            gitx.out(self.d, "rev-parse", "--verify", "nope")

    def test_out_without_check_returns_stdout(self):
        self.assertEqual(gitx.out(self.d, "rev-parse", "--verify", "nope", check=False), "")

    def test_try_out_none_on_failure(self):
        self.assertIsNone(gitx.try_out(self.d, "rev-parse", "--verify", "nope"))
        self.assertEqual(gitx.try_out(self.d, "rev-parse", "--is-inside-work-tree"), "true\n")

    def test_run_gives_the_process(self):
        self.assertNotEqual(gitx.run(self.d, "rev-parse", "--verify", "nope").returncode, 0)

    def test_run_input_reaches_stdin(self):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("skip.txt\n")
        r = gitx.run(self.d, "check-ignore", "-z", "--stdin", input="skip.txt\0keep.txt\0")
        self.assertEqual(r.stdout, "skip.txt\0")

    def test_timeout_is_named(self):
        hung = unittest.mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["git"], 5))
        with hung, self.assertRaisesRegex(RuntimeError, "timed out after 5 s"):
            gitx.out(self.d, "merge", "x")


if __name__ == "__main__":
    unittest.main()
