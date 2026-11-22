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
from pipeline import gate_record, worktree
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


def git_box(test_lines):
    d = box(test_lines)
    subprocess.run(["git", "init", "-q", d], check=True)
    return d


class TestRun(unittest.TestCase):
    def test_line_green_on_identical_tree_is_skipped_with_run_id(self):
        d = git_box("Test: true\n")
        gate_record.record(d, ["cs-qrun", "--", "true"], 0)
        run_id = gate_record._entries(gate_record._log_path(d))[-1]["run"]
        calls = []
        v, note = testrun.run("s", "task-1", d, runner=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a[0], 0, "", ""))
        self.assertEqual(v, "DONE")
        self.assertEqual(calls, [])
        self.assertIn(f"skipped true: identical passed tree, run {run_id}", note)

    def test_red_record_or_other_tree_runs_the_line(self):
        d = git_box("Test: true\n")
        gate_record.record(d, ["true"], 1)  # red run: never a skip
        calls = []
        v, _note = testrun.run("s", "task-1", d, runner=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a[0], 0, "", ""))
        self.assertEqual((v, len(calls)), ("DONE", 1))
        gate_record.record(d, ["true"], 0)
        with open(f"{d}/new.txt", "w") as f:  # tree changed after the green record: run again
            f.write("x")
        calls.clear()
        v, _note = testrun.run("s", "task-1", d, runner=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a[0], 0, "", ""))
        self.assertEqual((v, len(calls)), ("DONE", 1))

    def test_green_red_and_none(self):
        d = box("Test: `true`\n")
        self.assertEqual(testrun.run("s", "task-1", d)[0], "DONE")
        d = box("Test: sh -c 'echo boom; exit 3'\n")
        v, note = testrun.run("s", "task-1", d)
        self.assertEqual(v, "FAIL")
        self.assertIn("exited 3", note)
        self.assertIn("boom", note)
        self.assertIn("no Test line", testrun.run("s", "task-1", box("nothing\n"))[1])

    def test_gate_finding_fails_before_any_test_line_runs(self):
        calls = []
        v, note = testrun.run("s", "task-1", box("Test: true\n"), runner=lambda *a, **k: calls.append(a), scan=lambda cwd, d, stage: ["a.py:aws-key"])
        self.assertEqual(v, "FAIL")
        self.assertEqual(calls, [])
        self.assertIn("a.py:aws-key", note)

    def test_no_gate_finding_runs_the_test_lines(self):
        calls = []
        v, _note = testrun.run(
            "s",
            "task-1",
            box("Test: true\n"),
            runner=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a[0], 0, "", ""),
            scan=lambda cwd, d, stage: [],
        )
        self.assertEqual((v, len(calls)), ("DONE", 1))

    def test_scanner_error_is_a_note_and_never_blocks(self):
        def failed_scan(cwd, d, stage):
            return ["secret scan error: OSError"]

        calls = []
        v, note = testrun.run(
            "s", "task-1", box("Test: true\n"), runner=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a[0], 0, "", ""), scan=failed_scan
        )
        self.assertEqual((v, len(calls)), ("DONE", 1))
        self.assertIn("secret scan error: OSError", note)

    def test_scan_raising_is_a_note_and_never_blocks(self):
        def broken(cwd, d, stage):
            raise OSError("no git")

        calls = []
        v, note = testrun.run(
            "s", "task-1", box("Test: true\n"), runner=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a[0], 0, "", ""), scan=broken
        )
        self.assertEqual((v, len(calls)), ("DONE", 1))
        self.assertIn("gate scan could not run: no git", note)

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


class TestLeadVerified(unittest.TestCase):
    BRIEF = "Test: scripts/dev.sh test a.py\nTest: scripts/dev.sh docs-index  # lead-verified\n"

    def test_marked_test_line_is_lead_not_dev(self):
        self.assertEqual(testrun.test_lines(self.BRIEF), ["scripts/dev.sh test a.py"])
        self.assertEqual(testrun.lead_lines(self.BRIEF), ["scripts/dev.sh docs-index"])

    def test_marker_in_code_span_is_not_a_marker(self):
        text = "Goal: check `lead-verified` lines\nTest: scripts/dev.sh `lead-verified` test a.py\n"
        self.assertEqual(testrun.lead_lines(text), [])
        self.assertEqual(testrun.test_lines(text), ["scripts/dev.sh `lead-verified` test a.py"])

    def test_mid_command_marker_word_is_kept(self):
        text = "Test: scripts/dev.sh lead-verified a.py\n"
        self.assertEqual(testrun.lead_lines(text), ["scripts/dev.sh lead-verified a.py"])

    def test_lead_line_failure_is_fail_note_dev_stays_done(self):
        d = box(self.BRIEF)
        seen = []

        def runner(args, **k):
            seen.append(args)
            code = 1 if args[1] == "docs-index" else 0
            return subprocess.CompletedProcess(args, code, "", "docs check red")

        v, note = testrun.run("s", "task-1", d, runner=runner)
        self.assertEqual(v, "DONE")
        self.assertEqual(seen[-1], ["scripts/dev.sh", "docs-index"])
        self.assertIn("FAIL lead-verified `scripts/dev.sh docs-index` exited 1", note)

    def test_only_lead_lines_still_run(self):
        d = box("Test: scripts/dev.sh docs-index  # lead-verified\n")
        seen = []
        v, note = testrun.run("s", "task-1", d, runner=lambda a, **k: seen.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
        self.assertEqual(v, "DONE")
        self.assertEqual(len(seen), 1)
        self.assertNotIn("FAIL", note)


if __name__ == "__main__":
    unittest.main()
