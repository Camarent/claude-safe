import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.extra_tools import _has_version_token
from pipeline.plan_check import _repo_prefixed
from pipeline.plan_files import _is_phase_dir, section_body


class ScanHelpersTest(unittest.TestCase):
    def test_version_token(self):
        self.assertTrue(_has_version_token("tool 1.2.3\n", "1.2.3"))
        self.assertTrue(_has_version_token("tool v1.2.3", "1.2.3"))
        self.assertTrue(_has_version_token("v1.2.3", "1.2.3"))
        self.assertTrue(_has_version_token("v10.14.0\n", "10.14.0"))
        self.assertFalse(_has_version_token("tool 1.2.34", "1.2.3"))
        self.assertFalse(_has_version_token("tool 11.2.3", "1.2.3"))
        self.assertFalse(_has_version_token("tool 1.2.3.4", "1.2.3"))
        self.assertFalse(_has_version_token("tool xv1.2.3", "1.2.3"))

    def test_repo_prefixed(self):
        self.assertEqual(_repo_prefixed("a, kit/src/x.py", "kit"), "kit/s")
        self.assertEqual(_repo_prefixed("mykit/src", "kit"), "")
        self.assertEqual(_repo_prefixed("kit/ x then kit/y", "kit"), "kit/y")

    def test_phase_dir(self):
        self.assertTrue(_is_phase_dir("01-core"))
        self.assertFalse(_is_phase_dir("01-"))
        self.assertFalse(_is_phase_dir("1a-core"))
        self.assertFalse(_is_phase_dir("01-a\nb"))

    def test_section_body(self):
        text = "## A\nx\n## B \ny\nz\n## C\nw\n"
        self.assertEqual(section_body(text, lambda ln: ln.rstrip(" \t") == "## B", "## "), "y\nz")
        self.assertIsNone(section_body(text, lambda ln: ln == "## D", "## "))
        self.assertIsNone(section_body("## B", lambda ln: ln == "## B", "## "))


if __name__ == "__main__":
    unittest.main()
