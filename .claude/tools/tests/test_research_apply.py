"""research_apply: a scout report body is written to the one Outputs: path of a task and committed; nothing else changes."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import research_apply
from pipeline import verdicts
from pipeline.configuration import BLOCKED, DONE, ROLES, WORK_DIR
from pipeline.configuration.verdicts import SCOUT
from pipeline.util import work_path

STAGE, TASK = "stage-x", "task-1"
OUT = "out/report.md"
REPORT_TEXT = "VERDICT: DONE\n# Scout report\nbody line\n"


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)


class ResearchApply(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.root = os.path.join(tmp, "wt")
        self.proj = os.path.join(tmp, "proj")
        os.makedirs(self.root)
        os.makedirs(self.proj)
        _git(self.root, "init", "-q")
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.report = os.path.join(tmp, "report.md")
        self._write_report(REPORT_TEXT)
        self._write_task(f"Outputs: {OUT}\n")
        job = {"root": self.root, "branch": "job", "base": "main", "repos": []}
        job_patch = mock.patch.object(research_apply.worktrees, "job_info", return_value=job)
        job_patch.start()
        self.addCleanup(job_patch.stop)
        commit_patch = mock.patch.object(research_apply.worktree_merge, "commit", side_effect=self._fake_commit)
        self.commit = commit_patch.start()
        self.addCleanup(commit_patch.stop)

    def _write_report(self, text):
        with open(self.report, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _write_task(self, text):
        path = work_path(os.path.join(self.proj, WORK_DIR, STAGE), TASK + ".md", create=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {TASK}\n{text}")

    def _fake_commit(self, _a_dir, _stage, _task, subject, regen=None):
        """Stands in for worktree_merge.commit: commits every change of the one repo root, like it does."""
        if not research_apply.changed_paths(self.root):
            return {".": None}
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", subject)
        return {".": research_apply.gitx.run(self.root, "rev-parse", "--short", "HEAD").stdout.strip()}

    def _commit_with_docs_index(self, a_dir, stage, task, subject, regen=None):
        """Stands in for a commit whose docs index regeneration rewrites a second file into the same commit."""
        index = os.path.join(self.root, "docs", "INDEX.md")
        os.makedirs(os.path.dirname(index), exist_ok=True)
        with open(index, "w", encoding="utf-8") as fh:
            fh.write("# index\n")
        return self._fake_commit(a_dir, stage, task, subject, regen)

    def _apply(self):
        return research_apply.apply(self.proj, STAGE, TASK, self.report)

    def _head_paths(self):
        return research_apply.committed_paths(self.root)

    def test_body_written_and_committed_alone(self):
        code, line = self._apply()
        self.assertEqual(code, 0, line)
        self.assertTrue(line.startswith(DONE))
        with open(os.path.join(self.root, OUT), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "# Scout report\nbody line\n")
        self.assertEqual(self._head_paths(), {OUT})
        self.assertEqual(self.commit.call_count, 1)

    def test_verdict_line_dropped_only_first(self):
        self.assertEqual(research_apply.body_of("VERDICT: DONE\nA\nVERDICT: DONE\n\n"), "A\nVERDICT: DONE")

    def test_empty_body_fails(self):
        self._write_report("VERDICT: DONE\n\n")
        code, line = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("empty", line)
        self.assertEqual(self.commit.call_count, 0)

    def test_no_outputs_fails(self):
        self._write_task("")
        code, _ = self._apply()
        self.assertEqual(code, 1)
        self.assertEqual(self.commit.call_count, 0)

    def test_several_outputs_fail(self):
        self._write_task("Outputs: a.md, b.md\n")
        code, line = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("exactly one", line)
        self.assertEqual(self.commit.call_count, 0)

    def test_path_out_of_worktree_fails(self):
        self._write_task("Outputs: ../escape.md\n")
        code, _ = self._apply()
        self.assertEqual(code, 1)
        self.assertEqual(self.commit.call_count, 0)

    def test_extra_changed_file_fails_and_is_not_committed(self):
        with open(os.path.join(self.root, "stray.txt"), "w", encoding="utf-8") as fh:
            fh.write("not mine\n")
        code, line = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("stray.txt", line)
        self.assertEqual(self.commit.call_count, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, OUT)))

    def test_rerun_on_same_report_is_idempotent(self):
        self.assertEqual(self._apply()[0], 0)
        code, line = self._apply()
        self.assertEqual(code, 0, line)
        self.assertIn("already committed", line)
        self.assertEqual(self.commit.call_count, 1)

    def test_commit_skips_docs_index_regen(self):
        self.assertEqual(self._apply()[0], 0)
        self.assertIs(self.commit.call_args.kwargs["regen"], False)

    def test_docs_index_written_into_commit_fails(self):
        self.commit.side_effect = self._commit_with_docs_index
        code, line = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("commit changed", line)
        self.assertIn("docs/INDEX.md", line)

    def test_missing_report_fails_with_fail_line(self):
        os.remove(self.report)
        code, line = self._apply()
        self.assertEqual(code, 1)
        self.assertTrue(line.startswith("FAIL: no report file"), line)
        self.assertEqual(self.commit.call_count, 0)

    def test_worktrees_off_fails(self):
        with mock.patch.object(research_apply.worktrees, "job_info", return_value=None):
            code, line = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("worktrees are off", line)

    def test_scout_role_allows_done_or_blocked(self):
        self.assertEqual(ROLES[SCOUT], ((DONE, BLOCKED), BLOCKED))
        self.assertEqual(verdicts.verdict(SCOUT, "report written\nDONE")[0], DONE)

    def _main_output(self, code, line):
        out = io.StringIO()
        argv = ["--stage", STAGE, "--task", TASK, "--report", self.report]
        with mock.patch.object(research_apply, "apply", return_value=(code, line)), contextlib.redirect_stdout(out):
            self.assertEqual(research_apply.main(argv), code)
        return out.getvalue()

    def test_main_prints_a_verdict_line_the_driver_reads(self):
        done = self._main_output(0, "DONE: out/report.md committed abc")
        self.assertEqual(done.splitlines()[0], "VERDICT: DONE")
        self.assertEqual(verdicts.verdict("script", done), (DONE, "report line"))
        fail = self._main_output(1, "FAIL: empty body")
        self.assertEqual(fail.splitlines()[0], "VERDICT: FAIL")
        self.assertEqual(verdicts.verdict("script", fail)[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
