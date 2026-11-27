"""orch retry: a task whose brief or addendum names another task that is done but not merged gets a warning; orch.py/wave.py status list done-but-unmerged tasks."""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, cli_wave, graph

# fixed task ids: asserts compare these names, never a literal task id inside an assert
PREREQ = "task-33"
STEP = "task-26"
UNMERGED = "task-2"


class RetryPrereq(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/.work/s/task-26.md", "w", encoding="utf-8") as f:
            f.write("Test: true\nneeds the change of task-33 in tools/sim\n")
        with open(f"{self.t}/.work/s/orch.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"ev": "report", "task": "task-26", "id": "a1", "role": "dev", "verdict": "BLOCKED", "reason": "x"}) + "\n")
            f.write(json.dumps({"ev": "report", "task": "task-33", "id": "a2", "role": "dev", "verdict": "DONE", "reason": "x"}) + "\n")
        self.live: set = {"task-33"}
        self.merged: set = set()

    def retry(self, note="", step="task-26"):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step=step, note=note, from_step="", addendum="")
        out = io.StringIO()
        with (
            mock.patch.object(cli_orch, "_refresh_job"),
            mock.patch.object(cli_orch, "_reset_fix_chain"),
            mock.patch.object(cli_orch, "_blocked_dev_is_green", return_value=False),
            mock.patch.object(cli_orch.worktree, "live_job", side_effect=lambda d, s, t: {"root": "/x"} if t in self.live else None),
            mock.patch.object(cli_orch.worktree, "is_merged", side_effect=lambda d, s, t: t in self.merged),
            contextlib.redirect_stdout(out),
        ):
            cli_orch.cmd_reset(a)
        return json.loads(out.getvalue())

    def test_an_unmerged_named_task_is_warned_about_and_the_retry_still_happens(self):
        r = self.retry()
        self.assertIn(PREREQ, r["warn"])
        self.assertIn("not merged into the stage branch yet", r["warn"])
        self.assertEqual(r["recorded"], "retry")

    def test_the_note_counts_too_and_a_merged_or_gone_task_does_not_warn(self):
        os.remove(f"{self.t}/.work/s/task-26.md")
        with open(f"{self.t}/.work/s/task-26.md", "w", encoding="utf-8") as f:
            f.write("Test: true\n")
        note = f"edit .claude/tools/pipeline/cli_orch.py:523 also see {PREREQ}"
        self.assertIn(PREREQ, self.retry(note=note)["warn"])
        self.merged.add(PREREQ)
        self.assertNotIn("warn", self.retry(note=note))
        self.merged.clear()
        self.live.clear()
        self.assertNotIn("warn", self.retry(note=note))

    def test_a_task_that_is_not_done_yet_does_not_warn_and_an_error_never_aborts_the_retry(self):
        with open(f"{self.t}/.work/s/orch.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"ev": "report", "task": "task-26", "id": "a1", "role": "dev", "verdict": "BLOCKED", "reason": "x"}) + "\n")
        self.assertNotIn("warn", self.retry())  # task-33 has no dev DONE report: still running
        with open(f"{self.t}/.work/s/orch.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ev": "report", "task": "task-33", "id": "a2", "role": "dev", "verdict": "DONE", "reason": "x"}) + "\n")
        with mock.patch.object(cli_orch.worktree, "is_merged", side_effect=RuntimeError("git too old")):
            a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="task-26", note="", from_step="", addendum="")
            with (
                mock.patch.object(cli_orch, "_refresh_job"),
                mock.patch.object(cli_orch, "_reset_fix_chain"),
                mock.patch.object(cli_orch, "_blocked_dev_is_green", return_value=False),
                mock.patch.object(cli_orch.worktree, "live_job", return_value={"root": "/x"}),
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                cli_orch.cmd_reset(a)
        self.assertEqual(json.loads(out.getvalue())["recorded"], "retry")

    def test_the_task_itself_is_never_its_own_prerequisite(self):
        self.live = {"task-26"}
        self.assertNotIn("warn", self.retry(note=f"edit .claude/tools/pipeline/cli_orch.py:523 {STEP} again"))


class ResetChain(unittest.TestCase):
    def test_a_late_or_partial_merge_retry_resets_its_own_ladder(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        os.makedirs(f"{t}/.work/s")
        for step, want in (
            (
                "merge-w2-late2",
                {"fix-gate-w2-late2", "fix-gate-w2-late2-2", "merge-w2-late2-g1", "merge-w2-late2-g2", "merge-fix-w2-late2", "merge-w2-late2-2"},
            ),
            ("merge-w1-p4", {"fix-gate-w1-p4", "fix-gate-w1-p4-2", "merge-w1-p4-g1", "merge-w1-p4-g2", "merge-fix-w1-p4", "merge-w1-p4-2"}),
            ("merge-w3", {"fix-gate-w3", "fix-gate-w3-2", "merge-w3-g1", "merge-w3-g2"}),
        ):
            log = f"{t}/.work/s/orch.jsonl"
            open(log, "w").close()
            cli_orch._reset_fix_chain(log, argparse.Namespace(step=step))
            self.assertEqual({json.loads(x)["task"] for x in open(log) if x.strip()}, want, step)
        log = f"{t}/.work/s/orch.jsonl"
        open(log, "w").close()
        cli_orch._reset_fix_chain(log, argparse.Namespace(step="task-1"))
        self.assertEqual(open(log).read(), "")

    def test_status_shows_a_late_merge_done_once_its_remerge_is_done(self):
        evs = [
            {"ev": "dispatch", "task": "merge-w1-late", "id": "a1"},
            {"ev": "report", "task": "merge-w1-late", "id": "a1", "role": "script", "verdict": "FAIL"},
            {"ev": "dispatch", "task": "merge-w1-late-2", "id": "a2"},
            {"ev": "report", "task": "merge-w1-late-2", "id": "a2", "role": "script", "verdict": "DONE"},
        ]
        self.assertEqual(cli_wave._task_states(evs)["merge-w1-late"], "done")


class StatusLine(unittest.TestCase):
    def test_status_lists_done_but_unmerged_tasks(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        os.makedirs(f"{t}/.work/s")
        with open(f"{t}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write("# Stage: s\nGoal: g\n\n## Tasks\n- task-1 A - review: wave W1\n- task-2 B - review: wave W1\n\n## Graph\nwave 1: task-1, task-2\n")
        a = argparse.Namespace(cmd="status", stage="s", dir=t, feature=None)
        with open(f"{t}/.work/s/orch.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"ev": "report", "task": "task-1", "id": "a1", "role": "dev", "verdict": "DONE"}) + "\n")
            f.write(json.dumps({"ev": "dispatch", "task": "merge-w1", "id": "a9", "role": "script", "agent": "script", "model": None}) + "\n")
            f.write(json.dumps({"ev": "report", "task": "merge-w1", "id": "a9", "role": "script", "verdict": "DONE"}) + "\n")  # the wave merge ran
        out = io.StringIO()
        with mock.patch.object(graph, "_unmerged_done", return_value=["task-2"]), contextlib.redirect_stdout(out):
            cli_wave.cmd_status(a)
        self.assertIn(f"unmerged (done, job branch not merged into the stage branch): {UNMERGED}", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(graph, "_unmerged_done", return_value=[]), contextlib.redirect_stdout(out):
            cli_wave.cmd_status(a)
        self.assertNotIn("unmerged", out.getvalue())

    def test_a_done_task_whose_wave_merge_has_not_run_is_not_listed_and_a_git_error_does_not_break_status(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        os.makedirs(f"{t}/.work/s")
        with open(f"{t}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write("# Stage: s\nGoal: g\n\n## Tasks\n- task-2 B - review: wave W1\n\n## Graph\nwave 1: task-2\n")
        a = argparse.Namespace(cmd="status", stage="s", dir=t, feature=None)
        out = io.StringIO()
        with mock.patch.object(graph, "_unmerged_done", return_value=["task-2"]), contextlib.redirect_stdout(out):  # no merge-w1 event yet
            cli_wave.cmd_status(a)
        self.assertNotIn("unmerged", out.getvalue())
        with mock.patch.object(graph, "_unmerged_done", side_effect=RuntimeError("git")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_wave.cmd_status(a), 0)


class StatusIsReadOnly(unittest.TestCase):
    def test_status_never_removes_the_worktree_of_an_empty_done_job(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        os.makedirs(f"{t}/.work/s")
        with open(f"{t}/pipeline.json", "w", encoding="utf-8") as f:
            json.dump({"worktrees": True}, f)
        with open(f"{t}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write("# Stage: s\nGoal: g\n\n## Tasks\n- task-1 A - review: wave W1\n\n## Graph\nwave 1: task-1\n")
        with open(f"{t}/.work/s/orch.jsonl", "w", encoding="utf-8") as f:  # dev DONE, the reviewer is still running
            f.write(json.dumps({"ev": "report", "task": "task-1", "id": "a1", "role": "dev", "verdict": "DONE"}) + "\n")
            f.write(json.dumps({"ev": "dispatch", "task": "task-1", "id": "a2", "role": "reviewer", "agent": "reviewer", "model": "haiku"}) + "\n")
        a = argparse.Namespace(cmd="status", stage="s", dir=t, feature=None)
        out = io.StringIO()
        with (
            mock.patch.object(graph, "job_info", return_value={"root": t}),
            mock.patch.object(graph.worktree, "empty_job", return_value=True),
            mock.patch.object(graph.worktree, "remove") as rm,
            contextlib.redirect_stdout(out),
        ):
            cli_wave.cmd_status(a)
        rm.assert_not_called()
        self.assertNotIn("unmerged", out.getvalue())  # an empty job has nothing to merge


if __name__ == "__main__":
    unittest.main()
