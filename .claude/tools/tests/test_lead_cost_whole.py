import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import lead_cost


def turn(i, ts, cw=0, model="claude-sonnet-5-5"):
    u = {"input_tokens": 5, "cache_creation_input_tokens": cw, "cache_read_input_tokens": 0, "output_tokens": 10}
    return {"type": "assistant", "timestamp": ts, "message": {"id": f"m{i}", "model": model, "usage": u}}


def usd(path, lo=0.0, hi=float("inf")):
    return sum(lead_cost.analyse_lead(path, lo, hi)["usd"].values())


class WholeSessionTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = d.name
        self.lead_path = os.path.join(self.proj, "sid1.jsonl")
        self.sub = os.path.join(self.proj, "sid1", "subagents")
        os.makedirs(self.sub)

    def put(self, path, recs):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def test_sums_every_lead_turn_and_every_subagent_log(self):
        # the lead turn at 10:00 repeats its message id: counted once
        lead_turn = turn(1, "2026-11-05T10:00:00Z", cw=1_000_000)
        self.put(self.lead_path, [lead_turn, lead_turn, turn(2, "2026-11-05T20:00:00Z", cw=500_000)])
        self.put(os.path.join(self.sub, "agent-a.jsonl"), [turn(1, "2026-11-05T14:00:00Z", cw=1_000_000)])
        self.put(os.path.join(self.sub, "agent-b.jsonl"), [turn(1, "2026-11-06T09:00:00Z", cw=200_000)])
        got = lead_cost.whole_session([self.lead_path])
        agent_a = os.path.join(self.sub, "agent-a.jsonl")
        agent_b = os.path.join(self.sub, "agent-b.jsonl")
        self.assertAlmostEqual(got["lead_usd"], usd(self.lead_path))
        self.assertAlmostEqual(got["agent_usd"], usd(agent_a) + usd(agent_b))
        self.assertGreater(got["agent_usd"], 0)
        self.assertEqual((got["agents"], got["unmatched"]), (2, 0))

    def test_subagent_log_without_turns_is_unmatched(self):
        self.put(self.lead_path, [turn(1, "2026-11-05T10:00:00Z", cw=1_000_000)])
        self.put(os.path.join(self.sub, "agent-empty.jsonl"), [{"type": "user", "timestamp": "2026-11-05T10:05:00Z"}])
        got = lead_cost.whole_session([self.lead_path])
        self.assertEqual((got["agents"], got["unmatched"], got["agent_usd"]), (0, 1, 0.0))

    def test_no_subagents_dir_equals_analyse_lead_total(self):
        other = os.path.join(self.proj, "sid2.jsonl")
        self.put(other, [turn(1, "2026-11-05T10:00:00Z", cw=1_000_000), turn(2, "2026-11-05T11:00:00Z", cw=300_000)])
        got = lead_cost.whole_session([other])
        self.assertAlmostEqual(got["lead_usd"], sum(lead_cost.analyse_lead(other)["usd"].values()))
        self.assertEqual((got["agent_usd"], got["agents"], got["unmatched"]), (0.0, 0, 0))


class FeatureWholeLinesTest(unittest.TestCase):
    """The feature total gets the whole-session line; each phase line gets subagent cost only when some is inside its window."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.feature = os.path.join(self.root, "feat")
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(self.projects)
        self.sub = os.path.join(self.projects, "sid1", "subagents")
        os.makedirs(self.sub)
        folder = os.path.join(self.feature, "phases", "p1")
        os.makedirs(folder)
        with open(os.path.join(folder, "orch.jsonl"), "w", encoding="utf-8") as f:
            for ts in ("2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z"):
                f.write(json.dumps({"t": lead_cost.ledger.epoch(ts)}) + "\n")
        bash = {
            "type": "assistant",
            "entrypoint": "cli",
            "message": {"id": "b", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "wave.py run"}}]},
        }
        self.lead_path = os.path.join(self.projects, "sid1.jsonl")
        with open(self.lead_path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in [bash, turn(1, "2026-11-05T10:30:00Z", cw=1_000_000)]) + "\n")
        self.agent_path = os.path.join(self.sub, "agent-a.jsonl")
        with open(self.agent_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(turn(1, "2026-11-05T10:40:00Z", cw=1_000_000)) + "\n")

    def test_whole_line_exact_and_phase_gets_subagents(self):
        lead_usd, agent_usd = usd(self.lead_path), usd(self.agent_path)
        out = lead_cost.feature_lines(self.feature, [self.projects])
        self.assertEqual(
            out[2],
            f"   whole session incl. 1 subagent log(s): ${lead_usd + agent_usd:.2f} (leads ${lead_usd:.2f} + subagents ${agent_usd:.2f})",
        )
        self.assertEqual(out[-1], f"   phase p1: ${lead_usd:.2f}, 1 turns, subagents ${agent_usd:.2f}")

    def test_unmatched_log_listed_in_feature_line(self):
        # a log whose turns run past the phase window is not tied to it: counted unmatched
        with open(os.path.join(self.sub, "agent-part.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(turn(1, "2026-11-05T10:50:00Z", cw=1000)) + "\n" + json.dumps(turn(2, "2026-11-05T13:00:00Z", cw=1000)) + "\n")
        out = lead_cost.feature_lines(self.feature, [self.projects])
        self.assertTrue(out[2].endswith("; unmatched 1"))

    def test_phase_without_subagents_has_no_subagent_text(self):
        os.remove(self.agent_path)
        out = lead_cost.feature_lines(self.feature, [self.projects])
        self.assertNotIn("subagents $", out[-1])
        self.assertTrue(out[2].startswith("   whole session incl. 0 subagent log(s): $"))


if __name__ == "__main__":
    unittest.main()
