"""prereview: decide() branches, usage_context, job_diff, run() edge paths and main(); all in-process with injected fakes and temp dirs."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import prereview


class DecideTest(unittest.TestCase):
    def test_every_branch(self):
        self.assertEqual(prereview.decide(1, "FAIL: x a.py:3\n"), ("FAIL", "FAIL: x a.py:3"))
        self.assertEqual(prereview.decide(0, "skipped: no model"), ("SKIPPED", "local review skipped: no model"))
        self.assertEqual(prereview.decide(0, "ok"), ("DONE", "local review PASS: ok"))
        v, note = prereview.decide(2, "boom")
        self.assertEqual(v, "SKIPPED")
        self.assertIn("skipped (rc 2): boom", note)


class UsageContextTest(unittest.TestCase):
    def ctx(self, uses, text=False, paths=("src",), max_syms=8):
        ranges = {"src/a.py": [(1, 9)], "other/b.py": [(1, 9)], "src/c.py": []}
        with (
            mock.patch.object(prereview, "added_ranges", return_value=ranges),
            mock.patch.object(prereview, "lp", return_value="outline"),
            mock.patch.object(prereview, "definitions", return_value=[(3, "foo"), (4, "bar")]),
            mock.patch.object(prereview, "uses_of", return_value=(uses, text)),
        ):
            return prereview.usage_context("/d", list(paths), max_syms)

    def test_lists_uses_and_skips_other_paths(self):
        out = self.ctx(["u1", "u2"])
        self.assertIn("- foo (src/a.py:3): u1, u2", out)
        self.assertNotIn("other/b.py", out)

    def test_overflow_no_use_and_word_search_marker(self):
        self.assertIn("+2", self.ctx([f"u{i}" for i in range(7)]))
        self.assertIn("no other use", self.ctx([]))
        self.assertIn("- ~foo", self.ctx(["u"], text=True))

    def test_symbol_cap_and_empty(self):
        self.assertNotIn("bar", self.ctx(["u"], max_syms=1))
        self.assertEqual(self.ctx(["u"], paths=("nowhere",)), "")


class JobDiffTest(unittest.TestCase):
    def test_no_worktree_none(self):
        fake = mock.Mock(read_manifest=mock.Mock(return_value={}))
        with mock.patch.dict(sys.modules, {"wt": fake}):
            self.assertIsNone(prereview.job_diff("s", "task-1", "/d"))

    def test_diff_empty_marker_and_error(self):
        fake = mock.Mock(read_manifest=mock.Mock(return_value={"task-1": 1}), diff=mock.Mock(return_value="diff text"))
        with mock.patch.dict(sys.modules, {"wt": fake}):
            self.assertEqual(prereview.job_diff("s", "task-1", "/d"), "diff text")
            fake.diff.return_value = prereview.NO_COMMITS + " yet"
            self.assertEqual(prereview.job_diff("s", "task-1", "/d"), "")
            fake.diff.side_effect = RuntimeError("x")
            self.assertIsNone(prereview.job_diff("s", "task-1", "/d"))


class RunEdgeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        Path(self.d, ".work", "s").mkdir(parents=True)

    def brief(self):
        Path(self.d, ".work", "s", "task-1.md").write_text("Goal: demo\nPredicted paths: a.txt\n", encoding="utf-8")

    def test_missing_brief(self):
        v, note = prereview.run("s", "task-1", self.d)
        self.assertEqual(v, "SKIPPED")
        self.assertIn("local review skipped", note)

    def test_empty_diff(self):
        self.brief()
        _v, note = prereview.run("s", "task-1", self.d, git=lambda *a, **k: "  \n", review=mock.Mock(), context=lambda *a: "")
        self.assertIn("no diff", note)

    def test_context_failure_still_reviews_and_cleans_temp_files(self):
        self.brief()
        seen: dict[str, Any] = {}

        def review(d, *args, **kw):
            seen["files"] = [args[args.index("--brief") + 1], args[args.index("--diff") + 1]]
            seen["brief"] = Path(seen["files"][0]).read_text(encoding="utf-8")
            return 0, "all good"

        def bad_context(*a):
            raise ValueError("no lp")

        v, note = prereview.run("s", "task-1", self.d, git=lambda *a, **k: "diff --git x", review=review, context=bad_context)
        self.assertEqual((v, note), ("DONE", "local review PASS: all good"))
        self.assertEqual(seen["brief"], "Goal: demo\nPredicted paths: a.txt\n")
        self.assertFalse(any(os.path.exists(f) for f in seen["files"]))

    def test_main_prints_verdict(self):
        buf = io.StringIO()
        with mock.patch.object(prereview, "run", return_value=("FAIL", "bad")), contextlib.redirect_stdout(buf):
            self.assertEqual(prereview.main(["--stage", "s", "--task", "task-1", "--dir", self.d]), 0)
        self.assertEqual(buf.getvalue(), "VERDICT: FAIL\nbad\n")


if __name__ == "__main__":
    unittest.main()
