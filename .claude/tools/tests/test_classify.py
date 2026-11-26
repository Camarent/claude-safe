import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.classify import CODE, INFRA, PLAN, STOP, UNKNOWN, classify_test_run
from tests.support.tmpdirs import tracked_tmpdir

EMPTY = "SKIPPED/EMPTY: zero tests detected (0 tests)\n(log: x)"


class ClassifyTestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.repo = self.tmp.name
        self.stage = os.path.join(self.repo, ".work", "s")
        os.makedirs(self.stage)
        os.makedirs(os.path.join(self.repo, "tests"))

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, test, predicted):
        with open(os.path.join(self.stage, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: {predicted}\nTest: {test}\n")

    def test_wrong_glob_is_plan(self):
        open(os.path.join(self.repo, "tests", "a.test.js"), "w").close()
        self.task("node --test tests/*.test.mjs", "README.md")
        cls, why = classify_test_run(EMPTY, self.stage, "task-1", self.repo)
        self.assertEqual(cls, PLAN)
        self.assertIn("matches no file", why)

    def test_promised_test_file_missing_is_code(self):
        self.task("node --test tests/new.test.js", "tests/new.test.js")
        self.assertEqual(classify_test_run(EMPTY, self.stage, "task-1", self.repo)[0], CODE)

    def test_existing_file_with_no_tests_is_code(self):
        open(os.path.join(self.repo, "tests", "a.test.js"), "w").close()
        self.task("node --test tests/*.test.js", "README.md")
        self.assertEqual(classify_test_run(EMPTY, self.stage, "task-1", self.repo)[0], CODE)

    def test_skips_are_code(self):
        self.assertEqual(classify_test_run("SKIPPED/EMPTY: 2 skipped (5 tests)", self.stage, "task-1", self.repo)[0], CODE)

    def test_cannot_run_is_infra(self):
        self.assertEqual(classify_test_run("qrun: cannot run: [Errno 2]", self.stage, "task-1", self.repo)[0], INFRA)

    def test_test_named_approval_failing_is_code(self):
        out = "FAIL exit 1 (log: x)\nnot ok 3 - approval flow rejects stale token"
        self.assertEqual(classify_test_run(out, self.stage, "task-1", self.repo)[0], CODE)

    def test_script_not_executable_is_infra(self):
        self.assertEqual(classify_test_run("FAIL exit 126 (log: x)", self.stage, "task-1", self.repo)[0], INFRA)

    def test_test_named_cannot_run_is_code(self):
        out = "FAIL exit 1 (log: x)\nnot ok 2 - qrun: cannot run marker shows up in a test name"
        self.assertEqual(classify_test_run(out, self.stage, "task-1", self.repo)[0], CODE)

    def test_test_named_exit_127_is_code(self):
        out = "FAIL exit 1 (log: x)\nnot ok 4 - reports FAIL exit 127 when a binary is missing"
        self.assertEqual(classify_test_run(out, self.stage, "task-1", self.repo)[0], CODE)

    def test_test_named_exit_126_is_code(self):
        out = "FAIL exit 1 (log: x)\nnot ok 5 - shell gives FAIL exit 126 for a non-executable script"
        self.assertEqual(classify_test_run(out, self.stage, "task-1", self.repo)[0], CODE)

    def test_app_file_not_found_is_code(self):
        out = "FAIL exit 1 (log: x)\nFileNotFoundError: [Errno 2] No such file or directory: 'config.json'"
        self.assertEqual(classify_test_run(out, self.stage, "task-1", self.repo)[0], CODE)

    def test_real_failure_is_code(self):
        self.assertEqual(classify_test_run("not ok 1 - x\nFAIL", self.stage, "task-1", self.repo)[0], CODE)

    def test_no_signal_is_unknown_and_stops(self):
        cls = classify_test_run("hmm", self.stage, "task-1", self.repo)[0]
        self.assertEqual(cls, UNKNOWN)
        self.assertIn(cls, STOP)
        self.assertNotIn(CODE, STOP)


if __name__ == "__main__":
    unittest.main()
