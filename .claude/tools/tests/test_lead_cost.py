import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import lead_cost


def turn(i, ts, cw=0, cr=0, out=10, model="claude-sonnet-5-5"):
    u = {"input_tokens": 5, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}
    return {"type": "assistant", "timestamp": ts, "message": {"id": f"m{i}", "model": model, "usage": u}}


class LeadCostTest(unittest.TestCase):
    def write(self, recs):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "abcdef12-0000.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
        return p

    def test_gap_rewrite_and_split(self):
        p = self.write(
            [
                turn(1, "2026-11-05T10:00:00Z", cw=100_000),
                turn(2, "2026-11-05T10:01:00Z", cr=100_000),
                turn(3, "2026-11-05T11:30:00Z", cw=100_000),  # 89 min gap: cache expired, written again
            ]
        )
        r = lead_cost.analyse_lead(p)
        self.assertEqual((r["turns"], r["gaps"], r["long_gaps"], r["rewrite_tokens"]), (3, 1, 1, 100_000))
        self.assertAlmostEqual(r["usd"]["cache_write"], 2 * 100_000 * 2.5 / 1e6)
        self.assertAlmostEqual(r["rewrite"], 100_000 * (2.5 - 0.2) / 1e6)
        self.assertAlmostEqual(r["active"], 60.0)
        self.assertAlmostEqual(r["wall"], 5400.0)

    def test_repeated_message_id_counts_once(self):
        t = turn(1, "2026-11-05T10:00:00Z", cw=1000)
        r = lead_cost.analyse_lead(self.write([t, t]))
        self.assertEqual(r["turns"], 1)

    def test_lines_and_unpriced_model(self):
        p = self.write([turn(1, "2026-11-05T10:00:00Z", cw=10, model="mystery")])
        r = lead_cost.analyse_lead(p)
        self.assertEqual(r["unpriced"], 1)
        out = lead_cost.lead_lines([p])
        self.assertTrue(out[0].startswith("LEAD-COST abcdef12:"))
        self.assertTrue(out[-1].startswith("LEAD-COST total:"))
        self.assertEqual(lead_cost.lead_lines([]), [])

    def test_only_top_sessions_listed(self):
        paths = [self.write([turn(1, "2026-11-05T10:00:00Z", cw=1000 * (i + 1))]) for i in range(lead_cost.TOP_SESSIONS + 2)]
        out = lead_cost.lead_lines(paths)
        self.assertEqual(len(out), lead_cost.TOP_SESSIONS + 1)
        self.assertIn(f"in {len(paths)} session(s)", out[-1])

    def test_lead_is_interactive_and_runs_pipeline_tools(self):
        def bash(cmd, ep):
            return {"type": "assistant", "entrypoint": ep, "message": {"id": "b", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}

        lead = self.write([bash("python3 -I /root/.claude/tools/wave.py run --stage s", "cli")])
        headless = self.write([bash("python3 plan.py check", "sdk-cli")])
        chat = self.write([bash("ls", "cli")])
        self.assertTrue(lead_cost.is_lead(lead))
        self.assertFalse(lead_cost.is_lead(headless))
        self.assertFalse(lead_cost.is_lead(chat))
        self.assertEqual(lead_cost.lead_paths([lead, headless, chat]), [lead])

    def test_window_keeps_only_turns_inside(self):
        p = self.write([turn(1, "2026-11-05T10:00:00Z", cw=1000), turn(2, "2026-11-05T12:00:00Z", cw=1000)])
        lo = lead_cost.ledger.epoch("2026-11-05T11:00:00Z")
        self.assertEqual(lead_cost.analyse_lead(p, lo)["turns"], 1)


class LeadAgentRowsTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = d.name
        self.sub = os.path.join(self.proj, "sid1", "subagents")
        os.makedirs(self.sub)
        self.lo = lead_cost.ledger.epoch("2026-11-05T10:00:00Z")
        self.hi = lead_cost.ledger.epoch("2026-11-05T12:00:00Z")

    def put(self, path, recs):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def lead(self, ts="2026-11-05T10:30:00Z"):
        bash = {
            "type": "assistant",
            "entrypoint": "cli",
            "message": {"id": "b", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "wave.py run"}}]},
        }
        self.put(os.path.join(self.proj, "sid1.jsonl"), [bash, turn(1, ts, cw=1000)])

    def test_inside_window_matched_partial_counted_unmatched(self):
        self.lead()
        self.put(os.path.join(self.sub, "agent-in.jsonl"), [turn(1, "2026-11-05T10:40:00Z", cw=1000)])
        self.put(os.path.join(self.sub, "agent-part.jsonl"), [turn(1, "2026-11-05T11:00:00Z", cw=1000), turn(2, "2026-11-05T13:00:00Z", cw=1000)])
        self.put(os.path.join(self.sub, "agent-in.meta.json"), [{"agentType": "dev"}])
        rows, unmatched = lead_cost.lead_agent_rows([self.proj], self.lo, self.hi)
        self.assertEqual([(r["sid"], r["agent"], r["type"], r["turns"]) for r in rows], [("sid1", "agent-in", "dev", 1)])
        self.assertEqual(unmatched, 1)

    def test_lead_outside_window_has_no_rows(self):
        self.lead("2026-11-06T10:00:00Z")
        self.put(os.path.join(self.sub, "agent-in.jsonl"), [turn(1, "2026-11-05T10:40:00Z", cw=1000)])
        self.assertEqual(lead_cost.lead_agent_rows([self.proj], self.lo, self.hi), ([], 0))


class FeatureLinesTest(unittest.TestCase):
    """feature_lines never returns []: each zero-cost case names its reason."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.feature = os.path.join(self.root, "feat")
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(self.projects)

    def phase(self, name, stamps):
        folder = os.path.join(self.feature, "phases", name)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "orch.jsonl"), "w", encoding="utf-8") as f:
            for ts in stamps:
                f.write(json.dumps({"t": lead_cost.ledger.epoch(ts)}) + "\n")

    def lead(self, ts="2026-11-05T10:30:00Z", cw=1_000_000, name="sid1.jsonl"):
        bash = {
            "type": "assistant",
            "entrypoint": "cli",
            "message": {"id": "b", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "wave.py run"}}]},
        }
        with open(os.path.join(self.projects, name), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in [bash, turn(1, ts, cw=cw)]) + "\n")

    def test_no_window_reason(self):
        self.lead()
        self.assertEqual(lead_cost.feature_lines(self.feature, [self.projects]), ["LEAD-COST feature feat: $0.00, reason: no orch window"])

    def test_no_transcript_reason(self):
        self.phase("p1", ["2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z"])
        self.assertEqual(lead_cost.feature_lines(self.feature, [self.projects]), ["LEAD-COST feature feat: $0.00, reason: no lead transcript found"])

    def test_no_session_in_window_reason(self):
        self.phase("p1", ["2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z"])
        self.lead(ts="2026-11-06T10:00:00Z")
        self.assertEqual(lead_cost.feature_lines(self.feature, [self.projects]), ["LEAD-COST feature feat: $0.00, reason: no lead session in window"])

    def test_phase_with_no_cost_names_reason(self):
        self.phase("p1", ["2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z"])
        self.phase("p2", ["2026-11-05T14:00:00Z"])
        self.lead()
        out = lead_cost.feature_lines(self.feature, [self.projects])
        self.assertTrue(out[0].startswith("LEAD-COST feature feat: $"))
        self.assertTrue(out[-2].startswith("   phase p1: $"))
        self.assertNotIn("reason:", out[-2])
        self.assertEqual(out[-1], "   phase p2: $0.00, 0 turns, reason: no lead session in window")

    def test_phase_with_tiny_cost_names_reason(self):
        self.phase("p1", ["2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z"])
        self.lead(cw=1000)
        out = lead_cost.feature_lines(self.feature, [self.projects])
        self.assertEqual(out[-1], "   phase p1: $0.00, 1 turns, reason: lead turns cost under $0.005 or are on an unpriced model")

    def test_phase_with_no_transcript_names_reason(self):
        self.phase("p1", ["2026-11-05T10:00:00Z"])
        self.assertEqual(
            lead_cost._phase_line("p1", os.path.join(self.feature, "phases", "p1", "orch.jsonl"), []),
            "   phase p1: $0.00, 0 turns, reason: no lead transcript found",
        )


if __name__ == "__main__":
    unittest.main()
