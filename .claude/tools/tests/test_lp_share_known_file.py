"""lp_share.classify: a grep inside one named file is a known location, not a search; only unknown-location searches count toward the lp share."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import lp_share


class KnownFileTest(unittest.TestCase):
    def test_grep_in_one_file_is_not_counted(self):
        self.assertIsNone(lp_share.classify("Bash", {"command": "grep -n foo src/a.py"}))
        self.assertIsNone(lp_share.classify("Grep", {"pattern": "x", "path": "src/a.py"}))

    def test_grep_over_a_folder_or_repo_is_counted(self):
        self.assertEqual(lp_share.classify("Bash", {"command": "grep -rn foo src"}), "grep")
        self.assertEqual(lp_share.classify("Grep", {"pattern": "x"}), "grep")

    def test_lp_find_counts_and_share_ignores_known_file_greps(self):
        self.assertEqual(lp_share.classify("Bash", {"command": 'lp find "x"'}), "local:find")
        self.assertEqual(lp_share.share({"local:find": 1, "grep": 1}), 0.5)


if __name__ == "__main__":
    unittest.main()
