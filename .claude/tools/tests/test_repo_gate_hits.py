"""The new docs and test gates run clean over this repo: no bad cite in docs/, no empty test in .claude/tools/tests/. Decoys prove each check still reports a seeded bad case."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import empty_tests, steps_finaltest
from pipeline.configuration import DONE, FAIL

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(TESTS)))
SOURCE = "def alpha():\n    return 1\n"


def _empty_test_findings(tests_dir):
    found: list[str] = []
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(tests_dir, name)
        with open(path, encoding="utf-8") as f:
            found.extend(f"{name}:{line} {fn}" for fn, line in empty_tests.find(f.read(), path))
    return found


class RepoIsCleanTest(unittest.TestCase):
    def test_docs_cite_check_finds_nothing_in_this_repo(self):
        verdict, msgs = steps_finaltest.doc_cites(steps_finaltest.project(REPO), REPO)
        self.assertEqual((verdict, msgs), (DONE, []))

    def test_no_empty_test_in_this_repo(self):
        self.assertEqual(_empty_test_findings(TESTS), [])


class DecoyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_seeded_bad_cite_is_reported(self):
        self.write("src/mod.py", SOURCE)
        self.write("docs/guide.md", "# Guide\n\nSee `src/mod.py:99` for it.\n")
        verdict, msgs = steps_finaltest.doc_cites({"docs_dir": "docs"}, self.repo)
        self.assertEqual(verdict, FAIL)
        self.assertIn("docs/guide.md:3", msgs[0])

    def test_seeded_empty_test_is_reported(self):
        self.write("tests/test_decoy.py", "def test_nothing():\n    pass\n")
        self.assertEqual(_empty_test_findings(os.path.join(self.repo, "tests")), ["test_decoy.py:1 test_nothing"])


if __name__ == "__main__":
    unittest.main()
