"""related_files: the audit context's 'Related, unchanged' picks (model stubbed, no Strata, no network)."""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import related_files as rf
from search import strata
from tests.support.tmpdirs import tracked_mkdtemp


def make_repo():
    d = tracked_mkdtemp()
    files = {
        "src/a.py": "def foo():\n    pass\n",
        "src/b.py": "from a import foo\nfoo()\n",
        "tests/test_a.py": "import a\n",
        "docs/other.md": "nothing here\n",
        "CONTRACT.md": "unrelated\n",
    }
    for f, t in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
        with open(os.path.join(d, f), "w") as fh:
            fh.write(t)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    return d


class Related(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.calls: list = []
        self.answer: str | None = '{"files": ["src/b.py", "tests/test_a.py", "docs/other.md", "src/a.py", "ghost.py"]}'

        def call(prompt, max_tokens=0):
            self.calls.append(prompt)
            return self.answer

        self.p: list = [mock.patch.object(strata, "strata_up", return_value=True), mock.patch.object(strata, "strata_call", call)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_keeps_existing_unchanged_verified_only(self):
        out = rf.related_unchanged(self.repo, ["src/a.py"], {"src/a.py": ["foo"]})
        self.assertEqual(out, ["- src/b.py: uses foo (src/a.py)"])  # test_a.py (stem too short), other.md unverified; a.py in diff; ghost.py unknown

    def test_one_call_per_group_and_cache(self):
        rf.related_unchanged(self.repo, ["src/a.py", "docs/x.md"], {})
        self.assertEqual(len(self.calls), 2)
        rf.related_unchanged(self.repo, ["src/a.py", "docs/x.md"], {})
        self.assertEqual(len(self.calls), 2)  # second run from .code-index/ctx-cache.json

    def test_down_and_off(self):
        with mock.patch.object(strata, "strata_up", return_value=False):
            self.assertIsNone(rf.related_unchanged(self.repo, ["src/a.py"], {}))
        self.assertIsNone(rf.related_unchanged(self.repo, ["src/a.py"], {}, {"model": False, "ignore": [], "per_section": 5}))
        self.assertEqual(rf.related_unchanged(self.repo, [], {}), [])

    def test_bad_answer_gives_nothing(self):
        self.answer = "no json here"
        self.assertEqual(rf.related_unchanged(self.repo, ["src/a.py"], {"src/a.py": ["foo"]}), [])

    def test_line_cap(self):
        with mock.patch.object(rf, "MAX_LINES", 1):
            self.assertEqual(len(rf.related_unchanged(self.repo, ["src/a.py"], {"src/a.py": ["foo"]})), 1)

    def test_char_cap_and_unreadable(self):
        with mock.patch.object(rf, "MAX_CHARS", 10):
            self.assertEqual(rf.related_unchanged(self.repo, ["src/a.py"], {"src/a.py": ["foo"]}), [])
        self.assertEqual(rf.fact(self.repo, "nope.py", ["src/a.py"], {}), "")

    def test_most_named_first(self):
        votes = {"src/b.py": '{"files": ["tests/test_a.py"]}', "docs/x.md": '{"files": ["tests/test_a.py", "src/b.py"]}'}
        self.answer = None
        calls = iter(['{"files": ["src/b.py"]}', '{"files": ["tests/test_a.py", "src/b.py"]}'])
        with mock.patch.object(strata, "strata_call", lambda *a, **k: next(calls)):
            out = rf.candidates(self.repo, ["src/b.py", "tests/test_a.py", "src/a.py", "docs/x.md"], ["src/a.py", "docs/x.md"], {}, {"per_section": 5})
        self.assertEqual(out, ["src/b.py", "tests/test_a.py"])
        del votes


if __name__ == "__main__":
    unittest.main()
