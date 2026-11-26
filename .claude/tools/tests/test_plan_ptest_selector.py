"""plan_ptest: a ptest selector in a task's Test line must match a test file or a planned one."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_ptest


class PtestSelectorTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.d = os.path.join(self.repo, ".work", "s")
        os.makedirs(self.d)
        tests = os.path.join(self.repo, ".claude", "tools", "tests")
        os.makedirs(tests)
        open(os.path.join(tests, "test_real.py"), "w", encoding="utf-8").close()
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

    def test_planned_new_file_accepted(self):
        self.write("task-3.md", "# T\nPredicted paths: .claude/tools/tests/test_new.py\nTest: scripts/dev.sh test test_new.py\n")
        self.assertEqual(plan_ptest.ptest_selector_problems(self.d, "task-3", self.repo), [])

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
        self.write("task-3.md", "# T\nPredicted paths: src/x.py\n")
        self.assertEqual(plan_ptest.ptest_selector_problems(self.d, "task-3", self.repo), [])

    def test_absolute_pattern_is_a_problem_not_a_crash(self):
        got = self.problems("scripts/dev.sh test /abs/test_real.py")
        self.assertEqual(len(got), 1)
        self.assertIn("not a valid pattern", got[0])

    def test_wired_into_task_file_problems(self):
        self.write("task-2.md", "# T\nPredicted paths: src/x.py\nTest: scripts/dev.sh test test_real\n")
        probs = plan_check._task_file_problems(self.d, {"id": "task-2", "wave": 2, "depends": []}, [], self.repo, [], None, set())
        self.assertTrue(any("ptest selector test_real matches no test file" in p for p in probs), probs)
