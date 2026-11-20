"""stale_findings: cited lines changed by commits after the report are `maybe fixed`; a throwaway repo."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import stale_findings as sf
from tests.support.tmpdirs import tracked_mkdtemp

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
OLD, NEW = 1_700_000_000, 1_700_100_000  # commit times: before / after the report


class Stale(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.git("init", "-q", "-b", "master")
        self.write("a.py", "".join(f"l{i}\n" for i in range(1, 11)))
        self.commit("first", OLD)
        self.report = os.path.join(self.d, "audit.md")
        with open(self.report, "w", encoding="utf-8") as f:
            f.write("blocker a.py:3 bad\nshould-fix a.py:8 bad\nnote a.py:3 again, see docs/x.md:2\n")
        os.utime(self.report, (OLD + 100, OLD + 100))

    def git(self, *a, when=None):
        env = {**os.environ, **ENV, **({"GIT_AUTHOR_DATE": f"{when} +0000", "GIT_COMMITTER_DATE": f"{when} +0000"} if when else {})}
        subprocess.run(["git", "-C", self.d, *a], check=True, capture_output=True, env=env)

    def write(self, rel, text):
        with open(os.path.join(self.d, rel), "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, msg, when):
        self.git("add", "-A")
        self.git("commit", "-qm", msg, when=when)

    def test_cites_distinct(self):
        self.assertEqual(sf.cites(open(self.report, encoding="utf-8").read()), [("a.py", 3), ("a.py", 8), ("docs/x.md", 2)])

    def test_changed_line_is_maybe_fixed_others_open(self):
        self.write("a.py", "".join("fixed\n" if i == 3 else f"l{i}\n" for i in range(1, 11)))
        self.commit("fix", NEW)
        got = {(p, n): s for p, n, s in sf.stale_findings(self.report, self.d)}
        self.assertEqual(got[("a.py", 3)], sf.MAYBE_FIXED)
        self.assertEqual(got[("a.py", 8)], sf.OPEN)
        self.assertEqual(got[("docs/x.md", 2)], sf.OPEN)

    def test_commit_before_report_does_not_count(self):
        self.assertTrue(all(s == sf.OPEN for _p, _n, s in sf.stale_findings(self.report, self.d)))

    def test_missing_report_or_no_history(self):
        self.assertEqual(sf.stale_findings(os.path.join(self.d, "none.md"), self.d), [])
        os.utime(self.report, (OLD - 100, OLD - 100))
        self.assertEqual(sf.stale_findings(self.report, self.d), [])


if __name__ == "__main__":
    unittest.main()
