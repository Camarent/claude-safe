"""qualityrun.matches: only a leading './' is stripped from a finding path (no I/O)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import qualityrun


class TestMatches(unittest.TestCase):
    def test_leading_dot_names_match(self):
        self.assertTrue(qualityrun.matches(".eslintrc.js", ".eslintrc.js"))
        self.assertTrue(qualityrun.matches(".github/x.yml", ".github/x.yml"))

    def test_dot_slash_prefix_is_stripped(self):
        self.assertTrue(qualityrun.matches("./src/a.js", "src/a.js"))
        self.assertTrue(qualityrun.matches("./src/a.js", "pkg/src/a.js"))


if __name__ == "__main__":
    unittest.main()
