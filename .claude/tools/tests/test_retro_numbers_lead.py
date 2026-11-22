import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import feature_retro, retro_numbers


def epoch(iso):
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def turn(i, ts, cw=1_000_000):
    u = {"input_tokens": 5, "cache_creation_input_tokens": cw, "cache_read_input_tokens": 0, "output_tokens": 10}
    return {"type": "assistant", "timestamp": ts, "message": {"id": f"m{i}", "model": "claude-sonnet-5-5", "usage": u}}


def bash(cmd, ep="cli"):
    return {"type": "assistant", "entrypoint": ep, "message": {"id": "b", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}


def write_jsonl(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")


class StageLeadLinesTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.orch = os.path.join(self.root, "phases", "p1", "orch.jsonl")
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(self.projects)

    def stage(self, orch="", paths=None):
        return types.SimpleNamespace(orch=orch, paths=paths if paths is not None else [self.projects])

    def write_orch(self, *stamps):
        write_jsonl(self.orch, [{"ev": "start", "t": epoch(s)} for s in stamps])

    def test_no_orch_gives_zero_line(self):
        self.assertEqual(retro_numbers._stage_lead_lines(self.stage(orch="")), ["LEAD-COST stage: $0.00, reason: no orch"])

    def test_no_window_gives_zero_line(self):
        write_jsonl(self.orch, [{"ev": "start"}])  # no `t` stamp: window (0, 0)
        self.assertEqual(retro_numbers._stage_lead_lines(self.stage(self.orch)), ["LEAD-COST stage: $0.00, reason: no window"])

    def test_no_lead_turns_gives_zero_line(self):
        self.write_orch("2026-11-05T10:00:00Z", "2026-11-05T12:00:00Z")
        self.assertEqual(retro_numbers._stage_lead_lines(self.stage(self.orch)), ["LEAD-COST stage: $0.00, reason: no lead turns"])

    def test_lead_cost_gives_stage_lines_and_whole_session_last(self):
        self.write_orch("2026-11-05T10:00:00Z", "2026-11-05T12:00:00Z")
        lead = os.path.join(self.projects, "sid1.jsonl")
        write_jsonl(lead, [bash("python3 -I wave.py run --stage s"), turn(1, "2026-11-05T11:00:00Z")])
        write_jsonl(os.path.join(self.projects, "sid1", "subagents", "agent-a.jsonl"), [turn(2, "2026-11-05T11:30:00Z")])
        out = retro_numbers._stage_lead_lines(self.stage(self.orch))
        self.assertTrue(out[0].startswith("LEAD-COST sid1:"))
        self.assertTrue(out[-2].startswith("LEAD-COST total:"))
        self.assertTrue(out[-1].startswith("LEAD-COST whole session: $"))
        self.assertIn("incl. 1 subagent log(s)", out[-1])
        self.assertNotIn("; unmatched", out[-1])


class FeatureZeroLineTest(unittest.TestCase):
    def test_feature_retro_prints_zero_lead_lines_without_lead_log(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        feature = os.path.join(d.name, "F")
        write_jsonl(os.path.join(feature, "phases", "p1", "orch.jsonl"), [{"ev": "start", "t": epoch("2026-11-05T10:00:00Z")}])
        empty = os.path.join(d.name, "no-leads")
        os.makedirs(empty)
        out = os.path.join(d.name, "retro-input.md")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(feature_retro.main(["--feature-dir", feature, "--lead-log", empty, "--out", out]), 0)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## Lead cost of the whole feature", text)
        self.assertIn("LEAD-COST feature F: $0.00, reason: no lead transcript found", text)
