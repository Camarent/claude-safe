"""final_check.failing_ids: test ids read out of a gate's output."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import final_check


class FailingIdsTest(unittest.TestCase):
    def test_pytest_unittest_and_plain_lines(self):
        text = (
            "FAILED tests/a.py::test_x - boom\nFAIL: test_y (mod.Case.test_y)\nERROR: test_z (mod.Case)\nFAIL plain_name\nFAILED tests/a.py::test_x - again\n"
        )
        self.assertEqual(final_check.failing_ids(text), ["tests/a.py::test_x", "test_y (mod.Case.test_y)", "test_z (mod.Case)", "plain_name"])

    def test_no_id_gives_empty_and_the_list_is_capped(self):
        self.assertEqual(final_check.failing_ids("all good"), [])
        many = "\n".join(f"FAILED t.py::t{i}" for i in range(30))
        self.assertEqual(len(final_check.failing_ids(many)), 10)


if __name__ == "__main__":
    unittest.main()
