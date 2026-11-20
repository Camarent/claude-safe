"""retro/feature_retro.py: the problems file and the status line the driver turns into the step's verdict."""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import feature_retro


class FeatureRetroTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = t.name
        os.makedirs(os.path.join(self.dir, "phases", "01-a"))

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = feature_retro.main(["--feature-dir", self.dir, *args])
        return rc, out.getvalue()

    def test_ask_main_stops_are_counted_per_feature_by_cause(self):
        import json

        os.makedirs(os.path.join(self.dir, "phases", "02-b"))
        for phase, msgs in (
            ("01-a", ["task-1: dev verdict BLOCKED (report line); report /w/r1.md", "task-2: review failed 2 rounds; findings /w/f.md"]),
            ("02-b", ["task-9: dev verdict BLOCKED (report line); report /w/r9.md"]),
        ):
            with open(os.path.join(self.dir, "phases", phase, "orch.jsonl"), "w") as fh:
                fh.writelines(json.dumps({"ev": "ask", "message": m}) + "\n" for m in msgs)
                fh.write(json.dumps({"ev": "dispatch", "task": "task-1"}) + "\n")
        text = feature_retro.render("f", feature_retro.gather(self.dir))
        self.assertIn("## ask_main stops (3, per phase: 01-a 2, 02-b 1)", text)
        self.assertIn("- 2x dev verdict BLOCKED (report line)", text)
        self.assertIn("- 1x review failed 2 rounds", text)

    def test_out_file_run_prints_a_done_line(self):  # a silent --out run was recorded as FAIL ("no clear verdict") 4 of 4 times
        out = os.path.join(self.dir, "problems.md")
        rc, text = self.run_main("--out", out)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.getsize(out) > 0)
        self.assertTrue(text.startswith("VERDICT: DONE"))
        self.assertIn(out, text)

    def test_without_out_the_text_goes_to_stdout_unchanged(self):
        rc, text = self.run_main()
        self.assertEqual(rc, 0)
        self.assertTrue(text.startswith("# Feature problems"))

    def test_lead_log_adds_session_and_spawned_cost_lines(self):
        import json

        def msg(i):
            return {"type": "assistant", "message": {"id": i, "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000000, "output_tokens": 100000}}}

        proj = os.path.join(self.dir, "proj")
        sub = os.path.join(proj, "sid1", "subagents")
        os.makedirs(sub)
        for path in (os.path.join(proj, "sid1.jsonl"), os.path.join(sub, "agent-x.jsonl")):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(msg("m1")) + "\n")
        rc, text = self.run_main("--lead-log", proj)
        self.assertEqual(rc, 0)
        self.assertIn("lead-session sid1: 1100000 tokens", text)
        self.assertIn("lead-spawned agent-x", text)
        self.assertIn("$3.00", text)  # 1M in at $2 + 0.1M out at $10


if __name__ == "__main__":
    unittest.main()
