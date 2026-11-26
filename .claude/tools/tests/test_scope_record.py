"""Synthetic tests: the per-task `scope` line, the index-refresh script and the retro_numbers output dir."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [TOOLS]

import index_step  # noqa: E402
import wt  # noqa: E402


class ScopeRecord(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(f"{self.d}/.work/s")
        self.m = f"{self.d}/.work/s/metrics.jsonl"

    def line(self):
        with open(self.m, encoding="utf-8") as f:
            return json.loads(f.read().splitlines()[-1])

    def test_extra_is_edited_minus_predicted(self):
        with open(f"{self.d}/.work/s/task-1.md", "w", encoding="utf-8") as f:
            f.write("Predicted paths: `a.py`, docs/\n")
        wt._record_scope(self.d, "s", "task-1", "", ["a.py", "docs/x.md", "b.py", ""])
        r = self.line()
        self.assertEqual((r["ev"], r["task"], r["repo"]), ("scope", "task-1", "."))
        self.assertEqual(r["predicted"], ["a.py", "docs/"])
        self.assertEqual(r["edited"], ["a.py", "docs/x.md", "b.py"])
        self.assertEqual(r["extra"], ["b.py"])

    def test_decoy_prefix_is_not_a_match(self):
        with open(f"{self.d}/.work/s/task-1.md", "w", encoding="utf-8") as f:
            f.write("Predicted paths: docs\n")
        wt._record_scope(self.d, "s", "task-1", "n", ["docs2/x.md", "docs/y.md"])
        self.assertEqual(self.line()["extra"], ["docs2/x.md"])

    def test_sub_repo_edits_match_workspace_relative_predictions(self):
        with open(f"{self.d}/.work/s/task-1.md", "w", encoding="utf-8") as f:
            f.write("Predicted paths: n/src/a.py\n")
        wt._record_scope(self.d, "s", "task-1", "n", ["src/a.py", "src/b.py"])
        r = self.line()
        self.assertEqual(r["edited"], ["src/a.py", "src/b.py"])
        self.assertEqual(r["extra"], ["src/b.py"])

    def test_missing_brief_means_everything_is_extra(self):
        wt._record_scope(self.d, "s", "task-9", "", ["a.py"])
        self.assertEqual(self.line()["extra"], ["a.py"])


class IndexStep(unittest.TestCase):
    def test_roots_are_project_then_repos_with_an_index(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(f"{d}/withidx/.code-index")
        os.makedirs(f"{d}/plain")
        self.assertEqual(index_step.roots(d), [d, f"{d}/withidx"])

    def test_bloated_index_is_vacuumed_and_a_lean_one_is_left(self):
        import sqlite3
        from unittest import mock

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("a/.code-index", "b/c/.code-index", ".wt/x/.code-index"):
            os.makedirs(f"{d}/{name}")
        bloated, lean, skipped = f"{d}/a/.code-index/index.db", f"{d}/b/c/.code-index/index.db", f"{d}/.wt/x/.code-index/index.db"
        for db, drop in ((bloated, True), (lean, False), (skipped, True)):
            con = sqlite3.connect(db)
            con.execute("create table t(x blob)")
            con.executemany("insert into t values (?)", [(b"x" * 4000,) for _ in range(500)])
            con.commit()
            if drop:
                con.execute("delete from t")
                con.commit()
            con.close()
        self.assertEqual(sorted(index_step.index_dbs(d)), sorted([bloated, lean]))  # worktree folders are not walked
        with mock.patch.object(index_step, "VACUUM_MIN_BYTES", 1):
            before = os.path.getsize(bloated)
            self.assertIn("vacuumed", index_step.vacuum_bloated(bloated))
            self.assertLess(os.path.getsize(bloated), before)
            self.assertEqual(index_step.vacuum_bloated(lean), "")
        self.assertEqual(index_step.vacuum_bloated(f"{d}/missing.db"), "")

    def test_hung_index_run_is_a_failed_line_not_a_crash(self):
        import contextlib
        import io
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/.code-index")
            out = io.StringIO()
            hung = mock.patch.object(index_step.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1))
            argv = mock.patch.object(sys, "argv", ["index_step", "--dir", d])
            with hung, argv, contextlib.redirect_stdout(out):
                self.assertEqual(index_step.main(), 0)
        self.assertIn("VERDICT: FAIL", out.getvalue())
        self.assertIn("rc 124", out.getvalue())


if __name__ == "__main__":
    unittest.main()
