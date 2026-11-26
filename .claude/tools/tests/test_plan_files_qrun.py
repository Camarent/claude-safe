"""with_qrun: a Test command goes through qrun exactly once, whichever form the planner wrote."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_files
from pipeline.configuration import QRUN_WRAPPER


class WithQrunTest(unittest.TestCase):
    def test_plain_command_gets_the_path_form_once(self):
        out = plan_files.with_qrun("python3 -m pytest tests")
        self.assertEqual(out, plan_files.QRUN + "python3 -m pytest tests")
        self.assertEqual(out.count("qrun"), 1)

    def test_wrapper_form_is_left_alone(self):
        cmd = f"{QRUN_WRAPPER} -- scripts/dev/check-all.sh --py"
        self.assertEqual(plan_files.with_qrun(cmd), cmd)

    def test_path_form_is_left_alone(self):
        cmd = plan_files.QRUN + "scripts/dev/check-all.sh --py"
        self.assertEqual(plan_files.with_qrun(cmd), cmd)

    def test_name_that_only_starts_like_the_wrapper_is_wrapped(self):
        cmd = f"{QRUN_WRAPPER}ner -- ls"
        self.assertEqual(plan_files.with_qrun(cmd), plan_files.QRUN + cmd)

    def test_render_task_and_set_task_field_do_not_double_wrap(self):
        wrapped = f"{QRUN_WRAPPER} -- python3 -m pytest tests"
        txt = plan_files.set_task_field("Test: x\n", "task-1", "test", wrapped)
        self.assertEqual(txt, f"Test: {wrapped}\n")


if __name__ == "__main__":
    unittest.main()
