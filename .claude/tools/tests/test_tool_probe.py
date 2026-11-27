"""tool_probe: version smoke run, typescript peer range, knip entry config (M25 examples)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import tool_probe


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class ToolProbeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.js = os.path.join(self.root.name, "js")
        self.proj = os.path.join(self.root.name, "proj")
        os.makedirs(self.proj)

    def peer(self, tool, rng):
        write_json(os.path.join(self.js, "node_modules", tool, "package.json"), {"peerDependencies": {"typescript": rng}})

    def project_ts(self, rng):
        write_json(os.path.join(self.proj, "package.json"), {"devDependencies": {"typescript": rng}})

    def run_ok(self, rc=0, out="1.0.0"):
        calls = []

        def fake(cmd, _timeout=None):
            calls.append(cmd)
            return rc, out

        return fake, calls

    def test_madge_peer_five_with_project_seven_is_not_ok(self):
        self.peer("madge", "^5")
        self.project_ts("^7.0.0")
        fake, calls = self.run_ok()
        got = tool_probe.probe("madge", self.proj, run=fake, js=self.js)
        self.assertFalse(got.ok)
        self.assertEqual(got.reason, "madge peer typescript 5 vs project 7")
        self.assertEqual(calls[0], [f"{self.js}/node_modules/.bin/madge", "--version"])

    def test_peer_five_with_project_five_is_ok(self):
        self.peer("madge", "^5")
        self.project_ts("^5")
        fake, _ = self.run_ok()
        self.assertEqual(tool_probe.probe("madge", self.proj, run=fake, js=self.js), (True, ""))

    def test_non_zero_exit_returns_output_as_reason(self):
        fake, _ = self.run_ok(rc=1, out="boom")
        got = tool_probe.probe("eslint", self.proj, run=fake, js=self.js)
        self.assertEqual(got, (False, "boom"))

    def test_range_forms_are_parsed(self):
        self.assertEqual(tool_probe.majors(">=4 <6"), {4, 5})
        self.assertEqual(tool_probe.majors("5 || 6"), {5, 6})
        self.assertEqual(tool_probe.majors("~5.2"), {5})
        self.assertIsNone(tool_probe.majors("latest"))

    def test_project_inside_wider_peer_range_is_ok(self):
        self.peer("knip", ">=4 <6")
        self.project_ts("5.4.2")
        fake, _ = self.run_ok()
        self.assertTrue(tool_probe.probe("knip", self.proj, run=fake, js=self.js).ok)

    def test_unknown_peer_range_is_not_a_problem(self):
        self.peer("madge", "latest")
        self.project_ts("^7")
        fake, _ = self.run_ok()
        self.assertTrue(tool_probe.probe("madge", self.proj, run=fake, js=self.js).ok)

    def test_knip_without_entry_gives_reason(self):
        write_json(os.path.join(self.proj, "knip.json"), {"ignore": ["x.js"]})
        self.assertEqual(tool_probe.knip_entry_problem(self.proj), "knip config has no entry key")

    def test_knip_without_any_config_gives_reason(self):
        self.assertIn("knip config missing", tool_probe.knip_entry_problem(self.proj))

    def test_knip_entry_in_package_json_is_ok(self):
        write_json(os.path.join(self.proj, "package.json"), {"knip": {"entry": ["src/index.js"]}})
        self.assertEqual(tool_probe.knip_entry_problem(self.proj), "")

    def test_knip_json_with_entry_is_ok(self):
        write_json(os.path.join(self.proj, "knip.json"), {"entry": ["web/app.js"]})
        self.assertEqual(tool_probe.knip_entry_problem(self.proj), "")


if __name__ == "__main__":
    unittest.main()
