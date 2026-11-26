"""Swallowed errors in localmodel localdev, prereview: each FIX logs one `swallowed` stderr line and keeps its fallback.

Old code swallowed these silently, so each test fails there (no `swallowed in ...` line on stderr).
"""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wt
from localmodel import localdev, prereview
from tests.support.tmpdirs import tracked_mkdtemp


class IdentifierHitsSwallowTest(unittest.TestCase):
    def test_rg_failure_logs_and_gives_no_hits(self):
        root = tracked_mkdtemp()
        err = io.StringIO()
        with mock.patch.object(localdev.subprocess, "run", side_effect=OSError("rg missing")), contextlib.redirect_stderr(err):
            hits = localdev.identifier_hits(root, "change alpha_handler")
        self.assertEqual(hits, [])
        self.assertIn("swallowed in localdev.identifier_hits rg: OSError: rg missing", err.getvalue())

    def test_unreadable_hit_file_logs_and_skips_only_that_hit(self):
        root = tracked_mkdtemp()
        rg_out = subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{os.path.join(root, 'ghost.py')}:3:alpha_handler\n", stderr="")
        err = io.StringIO()
        with mock.patch.object(localdev.subprocess, "run", return_value=rg_out), contextlib.redirect_stderr(err):
            hits = localdev.identifier_hits(root, "change alpha_handler")
        self.assertEqual(hits, [])
        self.assertIn("swallowed in localdev.identifier_hits read: FileNotFoundError", err.getvalue())


class JobDiffSwallowTest(unittest.TestCase):
    def test_broken_job_worktree_logs_and_falls_back_to_none(self):
        d = tracked_mkdtemp()
        err = io.StringIO()
        with (
            mock.patch.object(wt, "read_manifest", return_value=["task-1"]),
            mock.patch.object(wt, "diff", side_effect=RuntimeError("boom")),
            contextlib.redirect_stderr(err),
        ):
            out = prereview.job_diff("s", "task-1", d)
        self.assertIsNone(out)
        self.assertIn("swallowed in prereview.job_diff: RuntimeError: boom", err.getvalue())


class ContextSwallowTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/.work/s")
        with open(f"{self.d}/.work/s/task-1.md", "w") as f:
            f.write("# t\nPredicted paths: src/\n")

    def test_failing_context_logs_and_review_runs_on_plain_brief(self):
        seen = []

        def review(repo, *a, **k):
            seen.append(a[a.index("--brief") + 1])
            return 0, "PASS"

        def boom(*a):
            raise RuntimeError("context down")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            v = prereview.run("s", "task-1", self.d, git=lambda *a, **k: "+x\n", review=review, context=boom)[0]
        self.assertEqual(v, "DONE")
        self.assertEqual(len(seen), 1)
        self.assertIn("swallowed in prereview.run context: RuntimeError: context down", err.getvalue())


if __name__ == "__main__":
    unittest.main()
