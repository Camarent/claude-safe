import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import worktree


class SubjectTest(unittest.TestCase):
    def test_no_trailing_newline_and_revert_wraps_a_valid_subject(self):
        self.assertTrue(worktree.subject_ok("feat(x): add thing"))
        self.assertFalse(worktree.subject_ok("feat(x): add thing\n"))
        self.assertTrue(worktree.subject_ok('Revert "feat(x): add thing"'))
        self.assertTrue(worktree.subject_ok('Reapply "Revert "fix(a): do thing""'))
        self.assertFalse(worktree.subject_ok('Revert "whatever goes here"'))
        self.assertFalse(worktree.subject_ok('Revert "feat(x): add thing"\n'))

    def test_predicted_suffix_needs_a_directory_part(self):
        self.assertFalse(worktree._predicted("sub/x.py", ["x.py"], ""))
        self.assertTrue(worktree._predicted("sub/x.py", ["sub/x.py"], ""))
        self.assertTrue(worktree._predicted("x.py", ["pkg/x.py"], "pkg") or worktree._predicted("x.py", ["x.py"], ""))
        self.assertTrue(worktree._predicted("lib/x.py", ["lib/x.py"], "pkg"))

    def test_absolute_predicted_matches_relative_edit(self):
        self.assertTrue(worktree._predicted("src/x.py", ["/ws/proj/n5/src/x.py"], "n5"))
        self.assertTrue(worktree._predicted("n5/src/x.py", ["/ws/proj/n5/src/x.py"], ""))
        self.assertTrue(worktree._predicted("src/a/x.py", ["/ws/proj/n5/src"], "n5"))
        self.assertFalse(worktree._predicted("src/y.py", ["/ws/proj/n5/src/x.py"], "n5"))
        self.assertFalse(worktree._predicted("x.py", ["/ws/proj/x.py"], "other"))

    def test_record_scope_strips_workspace_root(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".work", "s"))
            with open(os.path.join(d, ".work", "s", "t1.md"), "w") as fh:
                fh.write(f"Predicted paths: `{d}/n5/src/x.py`, `{d}/n5/doc/`\n")
            worktree._record_scope(d, "s", "t1", "n5", ["src/x.py", "doc/a.md", "other.py"])
            with open(os.path.join(d, ".work", "s", "metrics.jsonl")) as fh:
                rec = json.loads(fh.readline())
        self.assertEqual(rec["extra"], ["other.py"])


if __name__ == "__main__":
    unittest.main()
