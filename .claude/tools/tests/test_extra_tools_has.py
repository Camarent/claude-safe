"""extra_tools.has: the version must match as a token, 1.2 is not 11.2.4."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import extra_tools


class HasVersionTest(unittest.TestCase):
    def check(self, out, ver="1.2", kind="npm"):
        with mock.patch.object(extra_tools, "run", return_value=(0, out)):
            return extra_tools.has("tool", ver, kind)

    def test_substring_of_longer_version_is_not_a_match(self):
        self.assertFalse(self.check("11.2.4"))
        self.assertFalse(self.check("1.2.4"))
        self.assertFalse(self.check("v11.2"))

    def test_version_token_is_a_match(self):
        self.assertTrue(self.check("1.2"))
        self.assertTrue(self.check("tool v1.2 (node 20)"))
        self.assertTrue(self.check("1.2.0\n", ver="1.2.0"))

    def test_apt_needs_exact_line(self):
        self.assertFalse(self.check("1.2-3", kind="apt"))
        self.assertTrue(self.check("1.2", kind="apt"))


if __name__ == "__main__":
    unittest.main()
