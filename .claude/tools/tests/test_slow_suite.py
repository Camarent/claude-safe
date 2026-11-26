"""Slow-suite incident: a timed-out acceptance command is BLOCKED `timed out, not verified` (never FAIL), leaves one slow-suite row, and writes nothing else but the report."""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acceptance_step
from pipeline import acceptance
from pipeline.configuration import ACCEPTANCE_CMD_TIMEOUT_S, ACCEPTANCE_FILE, BLOCKED, FAIL, SLOW_SUITE_FILE
from pipeline.slow_suite import REASON, is_timeout, record
from tests.support.tmpdirs import tracked_mkdtemp
from tests.test_acceptance import make_repo, worktrees

PLAN_SLOW = "# Plan: f\n\n## Acceptance checklist\n- cmd: `slow`\n- cmd: `true`\n\n## Phases\n"
PLAN_SLOW_FAIL = "# Plan: f\n\n## Acceptance checklist\n- cmd: `slow`\n- cmd: `false`\n\n## Phases\n"
QRUN_TIMEOUT = (1, "qrun: timeout after 900 s (ORCH_SCRIPT_TIMEOUT)")
RUNNER_TIMEOUT = (127, "cannot run: Command 'sh -c slow' timed out after 960 seconds")
ROW_PATH = os.path.join("retro", SLOW_SUITE_FILE)


def runner_for(results, seen):
    """-> fake runner(cmd, cwd, timeout): answers RESULTS[cmd] (rc, text), (0, "ok") for an unlisted command; records each cmd in SEEN."""

    def runner(cmd, cwd, timeout):
        seen.append(cmd)
        return results.get(cmd, (0, "ok"))

    return runner


def _files(root):
    """-> relative paths of every file under ROOT."""
    out: set[str] = set()
    for base, _dirs, names in os.walk(root):
        out.update(os.path.relpath(os.path.join(base, n), root) for n in names)
    return out


def _rows(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class IsTimeoutTest(unittest.TestCase):
    def test_qrun_timeout_line_is_a_timeout(self):
        self.assertTrue(is_timeout(*QRUN_TIMEOUT))

    def test_runner_cannot_run_timed_out_is_a_timeout(self):
        self.assertTrue(is_timeout(*RUNNER_TIMEOUT))

    def test_plain_failure_is_not_a_timeout(self):
        self.assertFalse(is_timeout(1, "boom"))

    def test_pass_is_not_a_timeout_even_with_the_text(self):
        self.assertFalse(is_timeout(0, QRUN_TIMEOUT[1]))

    def test_cannot_run_without_a_timeout_is_not_a_timeout(self):
        self.assertFalse(is_timeout(127, "cannot run: [Errno 2] No such file"))


class RecordTest(unittest.TestCase):
    def test_one_row_per_call_and_folders_created(self):
        d = tracked_mkdtemp(prefix="slow-stage-")
        stage_dir = os.path.join(d, "stage")
        record(stage_dir, "proj", "acceptance", 901.04, 900)
        record(stage_dir, "proj", "acceptance", 902.0, 900)
        rows = _rows(os.path.join(stage_dir, ROW_PATH))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"kind": "slow-suite", "project": "proj", "step": "acceptance", "seconds": 901.0, "limit": 900})
        self.assertEqual(set(_files(stage_dir)), {ROW_PATH})


class TimeoutRunTest(unittest.TestCase):
    def test_timeout_alone_is_blocked_and_the_other_command_still_runs(self):
        d = make_repo(PLAN_SLOW)
        seen: list[str] = []
        v, text = acceptance.run(d, "f", "f", runner_for({"slow": QRUN_TIMEOUT}, seen))
        self.assertEqual(v, BLOCKED)
        self.assertTrue(text.startswith(REASON), text)
        self.assertEqual(seen, ["slow", "true"])
        self.assertEqual(len(worktrees(d)), 1)
        md = open(os.path.join(d, ".work", "f", ACCEPTANCE_FILE)).read()
        self.assertIn(f": {BLOCKED}\n\n- {REASON} exit 1: `slow`", md)
        self.assertIn("- timed out, not verified exit 1: `slow`", md)
        self.assertIn("PASS exit 0: `true`", md)

    def test_runner_cannot_run_timeout_is_blocked_too(self):
        d = make_repo(PLAN_SLOW)
        v, text = acceptance.run(d, "f", "f", runner_for({"slow": RUNNER_TIMEOUT}, []))
        self.assertEqual(v, BLOCKED)
        self.assertTrue(text.startswith(REASON), text)

    def test_timeout_writes_one_slow_suite_row_with_the_limit(self):
        d = make_repo(PLAN_SLOW)
        acceptance.run(d, "f", "f", runner_for({"slow": QRUN_TIMEOUT}, []))
        rows = _rows(os.path.join(d, ".work", "f", ROW_PATH))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "slow-suite")
        self.assertEqual(rows[0]["project"], os.path.basename(d))
        self.assertEqual(rows[0]["step"], "acceptance")
        self.assertEqual(rows[0]["limit"], ACCEPTANCE_CMD_TIMEOUT_S)
        self.assertGreaterEqual(rows[0]["seconds"], 0)

    def test_only_the_report_and_the_row_are_written(self):
        d = make_repo(PLAN_SLOW)
        stage_dir = os.path.join(d, ".work", "f")
        before = _files(stage_dir)
        acceptance.run(d, "f", "f", runner_for({"slow": QRUN_TIMEOUT}, []))
        self.assertEqual(_files(stage_dir) - before, {ACCEPTANCE_FILE, ROW_PATH})

    def test_real_failure_beside_a_timeout_stays_fail(self):
        d = make_repo(PLAN_SLOW_FAIL)
        seen: list[str] = []
        v, text = acceptance.run(d, "f", "f", runner_for({"slow": QRUN_TIMEOUT, "false": (1, "boom")}, seen))
        self.assertEqual(v, FAIL)
        self.assertTrue(text.startswith("1 of 2 commands failed"), text)
        self.assertEqual(seen, ["slow", "false"])


class StepTest(unittest.TestCase):
    def test_step_prints_blocked_and_the_reason_first(self):
        d = make_repo(PLAN_SLOW)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = acceptance_step.main(["--dir", d, "--stage", "f", "--name", "f"], runner=runner_for({"slow": QRUN_TIMEOUT}, []))
        lines = buf.getvalue().splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], f"VERDICT: {BLOCKED}")
        self.assertTrue(lines[1].startswith(REASON), lines[1])
