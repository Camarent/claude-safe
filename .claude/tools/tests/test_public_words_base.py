"""public-words gate: only hits on lines the stage diff (release-branch merge-base..HEAD) adds block; hits already on the base are a note."""

import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ops_steps
from pipeline import public_words
from tests.support.gitrepo import git

WORD = "sauce"
ENV = {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": WORD}
BASE_HIT = "the sauce was here before\n"
NEW_HIT = "the sauce is new\n"


class PublicWordsBaseTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = os.path.realpath(t.name)
        with open(os.path.join(self.d, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("This repo is public.\n")

    def _write(self, rel, text):
        with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _commit(self, msg):
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", msg)

    def _repo(self, base_text, stage_text):
        """master holds base_text in README.md; branch stage adds stage_text to it and commits."""
        git(self.d, "init", "-q", "-b", "master")
        self._write("README.md", base_text)
        self._commit("base")
        git(self.d, "checkout", "-q", "-b", "stage")
        self._write("README.md", base_text + stage_text)
        self._commit("stage")

    def test_c2_only_the_stage_added_hit_blocks_base_hit_is_a_note(self):
        self._repo(BASE_HIT, NEW_HIT)
        with unittest.mock.patch.dict(os.environ, ENV):
            ok, lines = ops_steps.public_words_gate(self.d)
        self.assertFalse(ok)
        self.assertEqual(lines[0], "README.md:2: sauce")
        self.assertEqual(lines[1], "add to docs/public-words-baseline.txt: README.md\tsauce\tthe sauce is new")
        self.assertEqual(lines[2:], ["base hits: 1 on lines the stage does not add (not blocking)", "base: README.md:1: sauce"])

    def test_c2_base_hit_only_gate_is_ok_with_a_note(self):
        self._repo(BASE_HIT, "a clean line\n")
        with unittest.mock.patch.dict(os.environ, ENV):
            ok, lines = ops_steps.public_words_gate(self.d)
        self.assertTrue(ok)
        self.assertEqual(lines, ["base hits: 1 on lines the stage does not add (not blocking)", "base: README.md:1: sauce"])

    def test_c3_baseline_entry_silences_a_stage_hit(self):
        self._repo(BASE_HIT, NEW_HIT)
        os.makedirs(os.path.join(self.d, "docs"), exist_ok=True)
        self._write(os.path.join("docs", "public-words-baseline.txt"), "README.md\tsauce\tthe sauce is new\n")
        with unittest.mock.patch.dict(os.environ, ENV):
            ok, lines = ops_steps.public_words_gate(self.d)
        self.assertTrue(ok)
        self.assertEqual(lines, ["base hits: 1 on lines the stage does not add (not blocking)", "base: README.md:1: sauce"])

    def test_c5_unresolvable_release_branch_fails_closed(self):
        self._repo(BASE_HIT, NEW_HIT)
        with (
            unittest.mock.patch.dict(os.environ, ENV),
            unittest.mock.patch.object(public_words, "release_branch", return_value="no-such-branch"),
        ):
            ok, lines = ops_steps.public_words_gate(self.d)
        self.assertFalse(ok)
        self.assertEqual(len(lines), 1)
        self.assertIn("no stage base", lines[0])

    def test_split_on_stage_separates_added_and_existing_lines(self):
        new = [("a.md", 1, "sauce", "x sauce"), ("a.md", 5, "sauce", "y sauce"), ("b.md", 2, "sauce", "z sauce")]
        added = {"a.md": [(5, "y sauce")]}
        blocking, on_base = public_words.split_on_stage(new, added)
        self.assertEqual(blocking, [("a.md", 5, "sauce", "y sauce")])
        self.assertEqual(on_base, [("a.md", 1, "sauce", "x sauce"), ("b.md", 2, "sauce", "z sauce")])
