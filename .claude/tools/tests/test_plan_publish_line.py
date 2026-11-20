"""plan.py run: a request's `PUBLISH:` line must reach TASKS.md (the driver's republish step reads only that file)."""

import argparse
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import TASKS_FILE, WORK_DIR
from pipeline.plan_run import publish_problems

REQUEST = "Goal: tap checkboxes.\nPUBLISH: https://claude.ai/artifact/abc\n"


class PublishLine(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="synthetic-publish-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.a = argparse.Namespace(dir=self.d, feature="feat")
        self.fdir = os.path.join(self.d, WORK_DIR, "feat")
        os.makedirs(self.fdir)

    def write_tasks(self, text):
        with open(os.path.join(self.fdir, TASKS_FILE), "w", encoding="utf-8") as f:
            f.write(text)

    def test_missing_line_is_a_problem(self):
        self.write_tasks("# Stage\n\nflags: ui\n")
        probs = publish_problems(self.a, REQUEST)
        self.assertEqual(len(probs), 1)
        self.assertIn("PUBLISH:", probs[0])
        self.assertIn(TASKS_FILE, probs[0])

    def test_copied_line_passes(self):
        self.write_tasks("# Stage\n\nPUBLISH: https://claude.ai/artifact/abc\n")
        self.assertEqual(publish_problems(self.a, REQUEST), [])

    def test_request_without_line_needs_nothing(self):
        self.write_tasks("# Stage\n")
        self.assertEqual(publish_problems(self.a, "Goal: docs only.\n"), [])

    def test_line_in_phase_tasks_passes(self):
        self.write_tasks("# Stage\n")
        phase = os.path.join(self.fdir, "phases", "01-tap")
        os.makedirs(phase)
        with open(os.path.join(phase, TASKS_FILE), "w", encoding="utf-8") as f:
            f.write("PUBLISH: https://claude.ai/artifact/abc\n")
        self.assertEqual(publish_problems(self.a, REQUEST), [])

    def test_second_request_line_must_also_be_carried(self):
        self.write_tasks("PUBLISH: https://claude.ai/artifact/abc\n")
        probs = publish_problems(self.a, REQUEST + "PUBLISH: https://claude.ai/artifact/second\n")
        self.assertEqual(len(probs), 1)
        self.assertIn("artifact/second", probs[0])

    def test_url_prefix_does_not_count_as_carried(self):
        self.write_tasks("PUBLISH: https://claude.ai/artifact/abc\n")
        self.assertEqual(len(publish_problems(self.a, "PUBLISH: https://claude.ai/artifact/abcd\n")), 1)


class RequestCopyAndAppend(PublishLine):
    def test_check_uses_request_copy(self):
        from pipeline.configuration import REQUEST_FILE
        from pipeline.plan_check import request_problems

        self.write_tasks("# Stage\n")
        self.assertEqual(request_problems(self.fdir), [])  # no request copy: nothing to compare
        with open(os.path.join(self.fdir, REQUEST_FILE), "w", encoding="utf-8") as f:
            f.write(REQUEST)
        self.assertEqual(len(request_problems(self.fdir)), 1)

    def test_ensure_publish_appends_once(self):
        from pipeline.plan_run import ensure_publish

        self.write_tasks("# Stage\nflags: ui")
        self.assertTrue(ensure_publish(self.a, REQUEST))
        self.assertEqual(publish_problems(self.a, REQUEST), [])
        self.assertFalse(ensure_publish(self.a, REQUEST))


class WaveNoPlan(unittest.TestCase):
    def test_no_plan_message_and_blocked_file(self):
        from pipeline import cli_feature
        from pipeline.configuration import BLOCKED_FILE, EXIT_BLOCKED

        d = tempfile.mkdtemp(prefix="synthetic-noplan-")
        self.addCleanup(shutil.rmtree, d, True)
        a = argparse.Namespace(dir=d, feature="feat")
        os.makedirs(os.path.join(d, WORK_DIR, "feat"))
        phase, rc = cli_feature._pick_phase(a, a)
        self.assertEqual((phase, rc), (None, EXIT_BLOCKED))
        with open(os.path.join(d, WORK_DIR, "feat", BLOCKED_FILE), encoding="utf-8") as f:
            self.assertIn("no plan: run plan.py run first", f.read())


class PlanBlocked(unittest.TestCase):
    def test_blocked_exit_and_file(self):
        from pipeline import cli_plan
        from pipeline.configuration import BLOCKED_FILE, EXIT_BLOCKED

        d = tempfile.mkdtemp(prefix="synthetic-blocked-")
        self.addCleanup(shutil.rmtree, d, True)
        a = argparse.Namespace(dir=d, feature="feat")
        self.assertEqual(cli_plan._blocked(a, "blocked: base is red: x"), EXIT_BLOCKED)
        with open(os.path.join(d, WORK_DIR, "feat", BLOCKED_FILE), encoding="utf-8") as f:
            self.assertIn("base is red", f.read())


class FetchRetry(unittest.TestCase):
    def test_retry_then_label(self):
        from audit import final_check
        from pipeline.configuration import FETCH_FAIL_LINE

        calls = []

        def runner(cmd, **kw):
            calls.append(1)
            return type("R", (), {"returncode": 1, "stdout": "Failed to consume the pack", "stderr": ""})()

        res = final_check._run_one("cargo audit", ".", {}, runner)
        self.assertEqual(len(calls), 2)
        self.assertTrue(res[3].startswith(FETCH_FAIL_LINE))

    def test_real_advisory_not_retried(self):
        from audit import final_check

        calls = []

        def runner(cmd, **kw):
            calls.append(1)
            return type("R", (), {"returncode": 1, "stdout": "RUSTSEC-2020-1 vulnerability", "stderr": ""})()

        final_check._run_one("cargo audit", ".", {}, runner)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
