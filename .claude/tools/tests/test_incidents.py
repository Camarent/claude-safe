import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import incidents
from tests.support.tmpdirs import tracked_tmpdir

PATCH_A = "diff --git a/src/a.js b/src/a.js\n--- a/src/a.js\n+++ b/src/a.js\n@@\n+x\n"
PATCH_B = "diff --git a/docs/b.md b/docs/b.md\n+y\n"


class IncidentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        base = Path(self.d, ".work", "s")
        (base / "patches").mkdir(parents=True)
        (base / "patches" / "task-1.patch").write_text(PATCH_A, encoding="utf-8")
        (base / "patches" / "task-2__sub_c.patch").write_text(PATCH_B, encoding="utf-8")
        (base / "task-1.md").write_text("spec of task 1", encoding="utf-8")
        (base / "reports").mkdir(exist_ok=True)
        (base / "reports" / "report-a9.md").write_text("FAIL: src/a.js:3 expected 1 got 2", encoding="utf-8")
        evs = [
            {"ev": "report", "id": "a1", "task": "task-1", "role": "reviewer", "verdict": "PASS"},
            {"ev": "report", "id": "a2", "task": "task-2", "role": "reviewer", "verdict": "FAIL"},
            {"ev": "report", "id": "a9", "task": "final-test", "role": "script", "verdict": "FAIL", "file": str(base / "reports" / "report-a9.md")},
            {"ev": "report", "id": "a10", "task": "final-test", "role": "script", "verdict": "DONE"},
        ]
        (base / "orch.jsonl").write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_matches_failure_text_to_the_task_patch(self):
        stubs = incidents.scan(self.d, "s")
        self.assertEqual(len(stubs), 1)  # the green final-test is no trigger
        self.assertEqual([c["task"] for c in stubs[0]["candidates"]], ["task-1"])
        self.assertEqual(stubs[0]["candidates"][0]["reviewer"], ["PASS"])
        self.assertTrue(os.path.exists(f"{self.d}/.work/s/retro/incidents.json"))
        self.assertIn("task-1 (review: PASS)", "\n".join(incidents.render(stubs, "s")))

    def test_scan_without_events_is_empty(self):
        self.assertEqual(incidents.scan(self.tmp.name + "/none", "x"), [])
        self.assertIn("none", incidents.render([], "x")[0])

    def test_keep_reviewer_miss_saves_patch_and_spec(self):
        incidents.scan(self.d, "s")
        path = incidents.keep(self.d, "s", "i1", "reviewer-miss", "off by one in a.js")
        self.assertTrue(path.endswith("s-i1.md"))
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("spec of task 1", text)
        self.assertIn("off by one", text)
        self.assertEqual(Path(self.d, ".notes", "incidents", "s-i1.patch").read_text(encoding="utf-8"), PATCH_A)

    def test_keep_not_a_miss_saves_no_patch(self):
        incidents.scan(self.d, "s")
        incidents.keep(self.d, "s", "i1", "not-a-miss", "flaky")
        self.assertFalse(Path(self.d, ".notes", "incidents", "s-i1.patch").exists())

    def test_keep_rejects_bad_label_and_unknown_id(self):
        incidents.scan(self.d, "s")
        with self.assertRaises(ValueError):
            incidents.keep(self.d, "s", "i1", "maybe", "x")
        with self.assertRaises(ValueError):
            incidents.keep(self.d, "s", "i9", "unclear", "x")


if __name__ == "__main__":
    unittest.main()
