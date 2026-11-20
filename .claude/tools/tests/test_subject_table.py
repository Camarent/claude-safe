"""Table of commit subjects: (subject, accepted by commit.py, accepted by worktree.py). Pins both validators across the merge into one implementation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import commit
from pipeline import worktree

TABLE = (
    ("feat(x): add thing", True, True),
    ("fix(a.b-c_d): do it now", True, True),
    ("feat(x): add thing\n", False, False),
    ("feat(x): add thing\nbody", False, False),
    ("feat(x): ", False, False),
    ("feat(x):  two spaces", False, False),
    ("feat(x): a", True, False),
    ("feat(x): abc", True, False),
    ("feat(x): abcd", True, True),
    ("feat: no area", False, True),
    ("feat(): empty area", False, False),
    ("feat(x)add", False, False),
    ("Feat(x): upper type", False, False),
    ("wip(x): not in the type list", False, True),
    ("hotfix(x): not in the type list", False, True),
    ("fix(a/b): slash in area", False, True),
    ("fix(-a): dash first", False, True),
    ("fix(.a): dot first", False, True),
    ("fix(a b): space in area", False, False),
    ("fix(é): unicode area", False, True),
    ('Revert "feat(x): add thing"', True, True),
    ('Reapply "fix(a): do thing"', True, True),
    ('Revert "Revert "fix(a): b c""', True, False),
    ('Revert "Revert "fix(a): do it""', True, True),
    ('Reapply "Revert "fix(a): do thing""', True, True),
    ('Revert "whatever goes here"', False, False),
    ('Revert ""', False, False),
    ('Revert " feat(x): add thing"', False, False),
    ('Revert "feat(x): add thing"\n', False, False),
    ('Revert "feat(x): add thing', False, False),
    ('Merge "feat(x): add thing"', False, False),
    ("", False, False),
    ("add thing", False, False),
)


class SubjectTable(unittest.TestCase):
    def test_commit_py(self):
        for s, want, _ in TABLE:
            with self.subTest(subject=s):
                self.assertEqual(commit.subject_ok(s), want)

    def test_worktree_py(self):
        for s, _, want in TABLE:
            with self.subTest(subject=s):
                self.assertEqual(worktree.subject_ok(s), want)


if __name__ == "__main__":
    unittest.main()
