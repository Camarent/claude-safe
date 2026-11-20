"""The final-webtest brief carries pipeline.json `webtest_build` only when it is set (synthetic config, no files)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs, phases


class A:
    stage = "s"
    dir = "."


class WebtestBuild(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name

    def brief(self, cfg):
        a = A()
        a.dir = self.d
        return str(phases._web_test_step(a, cfg, "task-1", "final-webtest", {"PASS"}))

    def test_set_key_names_command_and_commit(self):
        t = self.brief({"webtest_build": "make site"})
        self.assertIn("`make site`", t)
        self.assertIn("git rev-parse --short HEAD", t)

    def test_unset_key_leaves_brief_unchanged(self):
        t = self.brief({})
        self.assertNotIn("rev-parse", t)
        self.assertEqual(t, self.brief({"webtest_build": None}))


class NoteOnly(unittest.TestCase):
    def test_note_empty_when_unset(self):
        self.assertEqual(briefs.webtest_build_note({}), "")


if __name__ == "__main__":
    unittest.main()
