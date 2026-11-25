"""public_words_baseline.py: --write, --dry-run, --check and --words on a real tmp git repo."""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import public_words_baseline
from tests.support.gitrepo import git

BASELINE = os.path.join("docs", "public-words-baseline.txt")
HEADER = public_words_baseline.HEADER


class PublicWordsBaselineScriptTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = os.path.realpath(t.name)
        git(self.d, "init", "-q", "-b", "master")
        # scan order is line order, so "the" comes before "alpha": sorting must move "alpha" first
        self.put("README.md", "the sauce is here\nalpha sauce\nthe sauce is here\n")
        self.put("code.py", "x = sauce\n")
        git(self.d, "add", ".")
        git(self.d, "commit", "-q", "-m", "i")

    def put(self, rel, text):
        path = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_script(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = public_words_baseline.main([*args, "--dir", self.d])
        return rc, out.getvalue()

    def read_baseline(self):
        with open(os.path.join(self.d, BASELINE), encoding="utf-8") as fh:
            return fh.read()

    def test_write_sorts_and_dedupes_keys_under_header(self):
        rc, out = self.run_script("--write", "--words", "sauce")
        self.assertEqual(rc, 0)
        self.assertIn("wrote 3 keys", out)
        want = [*HEADER, "README.md\tsauce\talpha sauce", "README.md\tsauce\tthe sauce is here", "code.py\tsauce\tx = sauce"]
        self.assertEqual(self.read_baseline(), "\n".join(want) + "\n")

    def test_write_without_configured_words_writes_header_only(self):
        rc, _out = self.run_script("--write")
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_baseline(), "\n".join(HEADER) + "\n")

    def test_dry_run_counts_changes_and_writes_nothing(self):
        rc, out = self.run_script("--dry-run", "--words", "sauce")
        self.assertEqual(rc, 0)
        self.assertIn("3 baseline lines would change", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, BASELINE)))

    def test_dry_run_after_write_counts_zero(self):
        self.run_script("--write", "--words", "sauce")
        rc, out = self.run_script("--dry-run", "--words", "sauce")
        self.assertEqual(rc, 0)
        self.assertIn("0 baseline lines would change", out)

    def test_check_fails_on_hit_missing_from_baseline(self):
        rc, out = self.run_script("--check", "--words", "sauce")
        self.assertEqual(rc, 1)
        self.assertIn("code.py:1: sauce", out)
        self.assertIn("VERDICT: FAIL", out)

    def test_check_passes_after_write_and_ignores_stale_keys(self):
        self.run_script("--write", "--words", "sauce")
        self.put(BASELINE, self.read_baseline() + "gone.md\tsauce\tlong gone\n")
        rc, out = self.run_script("--check", "--words", "sauce")
        self.assertEqual(rc, 0)
        self.assertIn("VERDICT: DONE", out)

    def test_words_override_is_used_not_the_configured_setting(self):
        self.run_script("--write", "--words", "nomatch")
        self.assertEqual(self.read_baseline(), "\n".join(HEADER) + "\n")


if __name__ == "__main__":
    unittest.main()
