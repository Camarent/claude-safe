"""public_words baseline: records() tuples, the line-number-free hit key, read_baseline() and diff()."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import configuration, public_words


class T(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = td.name
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True, capture_output=True)

    def write(self, rel, text):
        """Write a tracked file (git add) under the repo."""
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True, capture_output=True)

    def test_setting_names_and_default(self):
        self.assertEqual(configuration.PUBLIC_WORDS_BASELINE_KEY, "public_words_baseline")
        self.assertEqual(configuration.PUBLIC_WORDS_BASELINE_DEFAULT, "docs/public-words-baseline.txt")

    def test_records_are_tuples_and_hits_keep_their_format(self):
        self.write("a.md", "intro\nthe Sauce here\n")
        self.assertEqual(public_words.records(self.repo, ["sauce"]), [("a.md", 2, "sauce", "the Sauce here")])
        self.assertEqual(public_words.hits(self.repo, ["sauce"]), ["a.md:2: sauce"])

    def test_records_empty_words_give_nothing(self):
        self.write("a.md", "sauce\n")
        self.assertEqual(public_words.records(self.repo, ["", "  "]), [])

    def test_hit_key_is_file_word_and_stripped_text_without_line_number(self):
        self.assertEqual(public_words.hit_key("a.md", "sauce", "  the sauce  "), "a.md\tsauce\tthe sauce")

    def test_key_stable_when_line_moves(self):
        self.write("a.md", "the sauce\n")
        before = public_words.records(self.repo, ["sauce"])
        baseline = {public_words.hit_key(r[0], r[2], r[3]) for r in before}
        self.write("a.md", "new first line\nthe sauce\n")
        after = public_words.records(self.repo, ["sauce"])
        self.assertEqual(after[0][1], 2)
        self.assertEqual(public_words.diff(after, baseline), ([], []))

    def test_duplicate_lines_share_one_key(self):
        self.write("a.md", "sauce\nsauce\n")
        found = public_words.records(self.repo, ["sauce"])
        self.assertEqual(len(found), 2)
        baseline = {public_words.hit_key("a.md", "sauce", "sauce")}
        self.assertEqual(public_words.diff(found, baseline), ([], []))

    def test_missing_baseline_file_is_empty(self):
        self.assertEqual(public_words.read_baseline(os.path.join(self.repo, "nope.txt")), set())

    def test_baseline_ignores_blank_and_comment_lines(self):
        path = os.path.join(self.repo, "base.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# accepted hits\n\na.md\tsauce\tthe sauce\n   \n# end\n")
        self.assertEqual(public_words.read_baseline(path), {"a.md\tsauce\tthe sauce"})

    def test_unreadable_baseline_raises_scan_error(self):
        # a directory exists at the baseline path: reading it fails with an OSError
        path = os.path.join(self.repo, "dir-as-baseline")
        os.makedirs(path)
        with self.assertRaises(public_words.ScanError):
            public_words.read_baseline(path)

    def test_stale_entry_is_reported(self):
        self.write("a.md", "clean text\n")
        stale_key = public_words.hit_key("gone.md", "sauce", "old sauce")
        new, stale = public_words.diff([], {stale_key})
        self.assertEqual(new, [])
        self.assertEqual(stale, [stale_key])

    def test_new_hit_is_reported(self):
        self.write("a.md", "the sauce\n")
        found = public_words.records(self.repo, ["sauce"])
        new, stale = public_words.diff(found, set())
        self.assertEqual(new, found)
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
