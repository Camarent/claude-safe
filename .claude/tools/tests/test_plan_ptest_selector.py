"""plan_ptest: a ptest selector in a task's Test line must match a test file or a planned one."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_ptest, plan_testroot


class PtestSelectorTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.d = os.path.join(self.repo, ".work", "s")
        os.makedirs(self.d)
        tests = os.path.join(self.repo, ".claude", "tools", "tests")
        os.makedirs(tests)
        open(os.path.join(tests, "test_real.py"), "w", encoding="utf-8").close()
        with open(os.path.join(tests, "golden_plan.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.write("TASKS.md", "# Stage: s\n## Tasks\n- task-1 first - review: wave W1\n- task-2 second - review: wave W2 - depends: task-1\n")
        self.write("task-1.md", "# T1\nPredicted paths: .claude/tools/tests/test_new.py\n")

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def problems(self, test_line, tid="task-2"):
        self.write(tid + ".md", f"# T\nPredicted paths: src/x.py\nTest: {test_line}\n")
        return plan_ptest.ptest_selector_problems(self.d, tid, self.repo)

    def test_name_without_py_rejected(self):
        got = self.problems("scripts/dev.sh test test_real")
        self.assertEqual(len(got), 1)
        self.assertIn("ptest selector test_real matches no test file", got[0])
        self.assertIn("end a ptest selector with .py or *", got[0])

    def test_existing_file_accepted(self):
        self.assertEqual(self.problems("scripts/dev.sh test test_real.py"), [])

    def test_glob_accepted(self):
        self.assertEqual(self.problems("scripts/dev.sh test 'test_*.py'"), [])

    def test_glob_matching_only_non_test_file_rejected(self):
        # a golden json in the tests dir is not a test file: ptest would select no test for it
        got = self.problems("scripts/dev.sh test 'golden_*.json'")
        self.assertEqual(len(got), 1)
        self.assertIn("ptest selector golden_*.json matches no test file", got[0])

    def test_glob_matching_no_file_rejected(self):
        self.assertEqual(len(self.problems("scripts/dev.sh test 'nothing_*.py'")), 1)

    def test_planned_non_test_file_does_not_satisfy_glob(self):
        self.write("solo.md", "# T\nPredicted paths: .claude/tools/tests/golden_new.json\nTest: scripts/dev.sh test 'golden_*.json'\n")
        self.assertEqual(len(plan_ptest.ptest_selector_problems(self.d, "solo", self.repo)), 1)

    def test_planned_new_file_accepted(self):
        self.write("solo.md", "# T\nPredicted paths: .claude/tools/tests/test_new.py\nTest: scripts/dev.sh test test_new.py\n")
        self.assertEqual(plan_ptest.ptest_selector_problems(self.d, "solo", self.repo), [])

    def test_planned_by_dependency_accepted(self):
        # task-2 depends on task-1, which predicts test_new.py
        self.assertEqual(self.problems("scripts/dev.sh test test_new.py"), [])

    def test_pytest_form_untouched(self):
        self.assertEqual(self.problems("scripts/dev.sh pytest test_real"), [])

    def test_ptest_direct_call_checked(self):
        self.assertEqual(len(self.problems("python3 -I .claude/tools/ptest.py test_real")), 1)

    def test_dir_option_honoured(self):
        other = os.path.join(self.repo, "other")
        os.makedirs(other)
        open(os.path.join(other, "test_x.py"), "w", encoding="utf-8").close()
        self.assertEqual(self.problems("scripts/dev.sh test --dir other test_x.py"), [])
        self.assertEqual(len(self.problems("scripts/dev.sh test --dir other test_x")), 1)

    def test_no_test_line_is_silent(self):
        self.write("solo.md", "# T\nPredicted paths: src/x.py\n")
        self.assertEqual(plan_ptest.ptest_selector_problems(self.d, "solo", self.repo), [])

    def test_absolute_pattern_is_a_problem_not_a_crash(self):
        got = self.problems("scripts/dev.sh test /abs/test_real.py")
        self.assertEqual(len(got), 1)
        self.assertIn("not a valid pattern", got[0])

    def test_wired_into_task_file_problems(self):
        self.write("task-2.md", "# T\nPredicted paths: src/x.py\nTest: scripts/dev.sh test test_real\n")
        probs = plan_check._task_file_problems(self.d, {"id": "task-2", "wave": 2, "depends": []}, [], self.repo, [], None, set())
        self.assertTrue(any("ptest selector test_real matches no test file" in p for p in probs), probs)


class VitestRootTest(unittest.TestCase):
    """plan_testroot: a vitest Test: path must not repeat the vitest root of pipeline.json test_roots."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.ws = td.name
        self.d = os.path.join(self.ws, ".work", "s")
        os.makedirs(self.d)

    def cfg(self, **kw):
        with open(os.path.join(self.ws, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(kw, f)

    def check(self, test_line):
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: a.ts\nTest: {test_line}\n")
        return plan_testroot.check_vitest_root(self.d, "task-1", self.ws)

    def test_vitest_path_repeating_root_is_an_error_naming_the_fixed_path(self):
        self.cfg(test_roots=[{"runner": "vitest", "root": "web/tests"}])
        got = self.check("`npx vitest run web/tests/foo.test.ts`")
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0].startswith("testroot:"), got)
        self.assertIn("use `foo.test.ts`", got[0])
        self.assertEqual(plan_check.errors_of(got), got)

    def test_root_with_leading_and_trailing_slashes_still_matched(self):
        self.cfg(test_roots=[{"runner": "vitest", "root": "/web/tests/"}])
        self.assertEqual(len(self.check("npx vitest run web/tests/a/b.test.ts")), 1)

    def test_dot_slash_path_repeating_root_is_an_error_naming_the_fixed_path(self):
        self.cfg(test_roots=[{"runner": "vitest", "root": "web/tests"}])
        got = self.check("npx vitest run ./web/tests/foo.test.ts")
        self.assertEqual(len(got), 1, got)
        self.assertIn("use `foo.test.ts`", got[0])

    def test_path_without_root_prefix_is_no_error(self):
        self.cfg(test_roots=[{"runner": "vitest", "root": "web/tests"}])
        self.assertEqual(self.check("npx vitest run foo.test.ts"), [])
        self.assertEqual(self.check("npx vitest run web/tests-extra/foo.test.ts"), [])

    def test_non_vitest_line_with_root_path_is_no_error(self):
        self.cfg(test_roots=[{"runner": "vitest", "root": "web/tests"}])
        self.assertEqual(self.check("scripts/dev.sh test web/tests/test_x.py"), [])
        self.assertEqual(self.check("pytest web/tests/foo.test.ts"), [])

    def test_no_test_roots_or_malformed_entries_is_no_error(self):
        self.cfg()
        self.assertEqual(self.check("npx vitest run web/tests/foo.test.ts"), [])
        self.cfg(test_roots="web/tests")
        self.assertEqual(self.check("npx vitest run web/tests/foo.test.ts"), [])
        self.cfg(test_roots=[{"runner": "vitest"}, {"runner": "vitest", "root": "/"}, "web/tests"])
        self.assertEqual(self.check("npx vitest run web/tests/foo.test.ts"), [])
