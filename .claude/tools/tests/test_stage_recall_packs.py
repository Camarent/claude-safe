"""stage_recall: phase packs (`### repo X` headings) and the finish phase measured against every pack of the feature."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import stage_recall as sr
from tests.support.tmpdirs import tracked_mkdtemp

PHASE_PACK = "## files\n### repo dojo-kit\n## s\n### code\n- a.py - d\n### repo n5 (nested: paths relative to it)\n## s\n### code\n- b.py\n"


class Sections(unittest.TestCase):
    def test_phase_pack_splits_by_repo_heading(self):
        d = os.path.join(tracked_mkdtemp(), "dojo-kit")
        nested = os.path.join(d, "ws", "n5")
        os.makedirs(nested)
        with mock.patch.object(sr, "pick_roots", return_value=[d, nested]):
            secs = sr.pack_sections(PHASE_PACK, d)
        self.assertEqual(sr.context_paths(secs[d]), ["a.py"])
        self.assertEqual(sr.context_paths(secs[nested]), ["b.py"])

    def test_single_repo_pack_is_whole(self):
        self.assertEqual(sr.pack_sections("## s\n### code\n- a.py\n", "/r"), {"/r": "## s\n### code\n- a.py\n"})


class ContextFiles(unittest.TestCase):
    def setUp(self):
        self.f = tracked_mkdtemp()
        for rel in ("plan-context.md", "phases/01-a/plan-context.md", "phases/02-b/plan-context.md", "phases/03-finish/TASKS.md"):
            p = Path(self.f, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")

    def test_phase_with_own_pack_uses_it_alone(self):
        got = sr.find_context_files(os.path.join(self.f, "phases", "02-b"))
        self.assertEqual(got, [os.path.join(self.f, "phases", "02-b", "plan-context.md")])

    def test_finish_phase_gets_feature_and_all_phase_packs(self):
        got = sr.find_context_files(os.path.join(self.f, "phases", "03-finish"))
        self.assertEqual(
            got,
            [
                os.path.join(self.f, "plan-context.md"),
                os.path.join(self.f, "phases", "01-a", "plan-context.md"),
                os.path.join(self.f, "phases", "02-b", "plan-context.md"),
            ],
        )

    def test_none(self):
        self.assertEqual(sr.find_context_files(tracked_mkdtemp()), [])


class Union(unittest.TestCase):
    def test_paths_of_every_pack_count(self):
        packs = ["## s\n### code\n- a.py\n", "## s\n### code\n- b.py\n"]
        self.assertEqual(sorted(sr.context_paths(sr.merge_packs(packs, "/r")["/r"])), ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
