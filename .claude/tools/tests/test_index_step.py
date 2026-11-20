"""index_step: roots, verdict and the timeout / cannot-run paths, with a fake subprocess."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import index_step


class IndexStepTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = os.path.realpath(tmp.name)
        os.makedirs(os.path.join(self.d, "sub", ".code-index"))
        os.makedirs(os.path.join(self.d, "plain"))

    def run_main(self, side_effect):
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["index_step.py", "--dir", self.d]),
            mock.patch.object(index_step.subprocess, "run", side_effect=side_effect) as run,
            contextlib.redirect_stdout(buf),
        ):
            rc = index_step.main()
        return rc, buf.getvalue().splitlines(), run

    def test_roots_are_the_project_and_nested_indexed_repos(self):
        self.assertEqual(index_step.roots(self.d), [self.d, os.path.join(self.d, "sub")])

    def test_all_green(self):
        ok = SimpleNamespace(returncode=0, stdout="indexed 3 files\n", stderr="")
        rc, out, run = self.run_main(lambda *a, **k: ok)
        self.assertEqual(rc, 0)
        self.assertEqual(out[0], "VERDICT: DONE")
        self.assertEqual(out[1], f"{self.d}: rc 0 indexed 3 files")
        self.assertEqual(run.call_count, 2)

    def test_timeout_and_missing_interpreter_fail_with_a_reason(self):
        rc, out, _ = self.run_main([subprocess.TimeoutExpired("x", 1), OSError("nope")])
        self.assertEqual(rc, 0)
        self.assertEqual(out[0], "VERDICT: FAIL")
        self.assertIn("rc 124 timed out after", out[1])
        self.assertIn("rc 127 cannot run: nope", out[2])


if __name__ == "__main__":
    unittest.main()
