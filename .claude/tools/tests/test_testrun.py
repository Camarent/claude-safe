import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testrun
from pipeline import worktree
from pipeline.classify import classify_test_run
from tests.support.tmpdirs import tracked_mkdtemp


def box(test_lines, worktrees=False):
    d = tracked_mkdtemp()
    os.makedirs(f"{d}/.work/s")
    with open(f"{d}/.work/s/task-1.md", "w") as f:
        f.write("# t\n" + test_lines)
    if worktrees:
        with open(f"{d}/pipeline.json", "w") as f:
            json.dump({"worktrees": True}, f)
    return d


class TestRun(unittest.TestCase):
    def test_green_red_and_none(self):
        d = box("Test: `true`\n")
        self.assertEqual(testrun.run("s", "task-1", d)[0], "DONE")
        d = box("Test: sh -c 'echo boom; exit 3'\n")
        v, note = testrun.run("s", "task-1", d)
        self.assertEqual(v, "FAIL")
        self.assertIn("exited 3", note)
        self.assertIn("boom", note)
        self.assertIn("no Test line", testrun.run("s", "task-1", box("nothing\n"))[1])

    def test_never_runs_on_main_checkout_when_worktrees_are_on(self):
        calls = []
        v, _note = testrun.run("s", "task-1", box("Test: true\n", worktrees=True), runner=lambda *a, **k: calls.append(a))
        self.assertEqual(v, "BLOCKED")
        self.assertEqual(calls, [])

    def test_runs_in_the_job_worktree(self):
        d = box("Test: pwd\n", worktrees=True)
        wtree = tracked_mkdtemp()
        os.makedirs(f"{d}/.wt/s")
        seen = []
        orig = worktree.read_manifest
        worktree.read_manifest = lambda *_: {"task-1": {"root": wtree}}
        try:
            testrun.run("s", "task-1", d, runner=lambda c, **k: seen.append(k["cwd"]) or subprocess.CompletedProcess(c, 0, "", ""))
        finally:
            worktree.read_manifest = orig
        self.assertEqual(seen, [wtree])

    def test_missing_command_or_module_is_blocked_not_red(self):
        d = box("Test: nosuchcmd\n")
        self.assertEqual(testrun.run("s", "task-1", d)[0], "BLOCKED")  # real runner: OSError (no such file)
        v, note = testrun.run("s", "task-1", box("Test: x\n"), runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 127, "", "x: command not found"))
        self.assertEqual(v, "BLOCKED")
        self.assertIn("127", note)
        v, _ = testrun.run(
            "s", "task-1", box("Test: x\n"), runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "ModuleNotFoundError: No module named x")
        )
        self.assertEqual(v, "BLOCKED")
        v, _ = testrun.run("s", "task-1", box("Test: x\n"), runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 126, "", ""))
        self.assertEqual(v, "BLOCKED")

    def test_write_through_a_nested_repo_link_fails_and_never_runs_the_tests(self):
        d = box("Test: true\n", worktrees=True)
        calls = []
        with (
            mock.patch.object(testrun.wt, "live_job", return_value={"root": d}),
            mock.patch.object(testrun.linkguard, "check", return_value="FAIL: path outside this task's worktree: x") as chk,
        ):
            v, note = testrun.run("s", "task-1", d, runner=lambda *a, **k: calls.append(a))
        self.assertEqual((v, calls), ("FAIL", []))
        self.assertIn("path outside this task's worktree", note)
        self.assertIn("add-repo --stage s --task task-1", chk.call_args[0][3])  # the dev is told the exact command
        self.assertEqual(classify_test_run(f"VERDICT: FAIL\n{note}", d, "task-1", d)[0], "code")  # a dev rework, not a lead stop

    def test_no_linked_write_runs_the_tests(self):
        d = box("Test: true\n", worktrees=True)
        with (
            mock.patch.object(testrun.wt, "live_job", return_value={"root": d}),
            mock.patch.object(testrun.linkguard, "check", return_value=""),
        ):
            self.assertEqual(testrun.run("s", "task-1", d)[0], "DONE")

    def test_plain_nonzero_exit_stays_fail(self):
        v, note = testrun.run("s", "task-1", box("Test: x\n"), runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "AssertionError", ""))
        self.assertEqual(v, "FAIL")
        self.assertIn("exited 1", note)

    def test_unreadable_brief_is_blocked_not_green(self):
        d = box("Test: true\n")
        os.remove(f"{d}/.work/s/task-1.md")
        os.makedirs(f"{d}/.work/s/task-1.md")  # a directory: open() fails
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(testrun.run("s", "task-1", d)[0], "BLOCKED")

    def test_missing_module_imported_by_the_change_is_red_else_blocked(self):
        d = box("Test: x\n")
        self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)
        with open(f"{d}/mine.py", "w", encoding="utf-8") as f:
            f.write("import nosuchlib\n")
        err = lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "ModuleNotFoundError: No module named 'nosuchlib'")  # noqa: E731
        self.assertEqual(testrun.run("s", "task-1", d, runner=err)[0], "FAIL")  # the dev's own bad import
        with open(f"{d}/mine.py", "w", encoding="utf-8") as f:
            f.write("import os\n")
        self.assertEqual(testrun.run("s", "task-1", d, runner=err)[0], "BLOCKED")  # not imported by the change

    def test_timeout_is_blocked(self):
        def slow(*a, **k):
            raise subprocess.TimeoutExpired(a[0], k["timeout"])

        self.assertEqual(testrun.run("s", "task-1", box("Test: x\n"), runner=slow)[0], "BLOCKED")


class WorkdirRepo(unittest.TestCase):
    def test_repo_folder_when_worktrees_off(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "pipeline.json").write_text('{"repo": "r"}')
            self.assertEqual(testrun.workdir(d, "s", "t1"), os.path.join(d, "r"))
            Path(d, "pipeline.json").write_text("{}")
            self.assertEqual(testrun.workdir(d, "s", "t1"), d)


class TestRunMain(unittest.TestCase):
    def test_main_prints_verdict_and_note(self):
        d = box("Test: `true`\n")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(testrun.main(["--stage", "s", "--task", "task-1", "--dir", d]), 0)
        self.assertEqual(out.getvalue().splitlines()[0], "VERDICT: DONE")
        self.assertIn("Test line(s) green", out.getvalue())

    def test_command_not_found_exit_is_blocked_not_red(self):
        d = box("Test: sh -c 'exit 127'\n")
        v, note = testrun.run("s", "task-1", d)
        self.assertEqual(v, "BLOCKED")
        self.assertIn("missing command", note)


if __name__ == "__main__":
    unittest.main()
