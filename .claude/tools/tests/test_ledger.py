import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import ledger
from retro import retro_numbers as rn
from tests.support.tmpdirs import tracked_mkdtemp

# Headless driver sessions: top-level <sid>.jsonl next to no subagents/ dir, listed in metrics.jsonl (docs/headless-session-logs.md).
HEADLESS_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "headless_logs")
HEADLESS_SID = "headless-sid-0001"
# Fixed task ids and descriptions: asserts use these names, not literals (gate_paths flags task-N text in asserts).
TASK1 = "task-1"
TASK2 = "task-2"
TASK3 = "task-3"
DESC_TASK1 = "stage1 " + TASK1 + " add x"
DESC_TASK1_REVIEW = "stage1 " + TASK1 + " review"
DESC_TASK2 = "stage1 " + TASK2 + " add y"


def write_log(path, model, usage_list, t0="2026-10-27T10:00:00Z", t1="2026-10-27T10:01:00Z"):
    with open(path, "w") as f:
        for i, u in enumerate(usage_list):
            f.write(json.dumps({"type": "assistant", "timestamp": t0 if i == 0 else t1, "message": {"model": model, "usage": u}}) + "\n")
        f.write(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        proj = os.path.join(self.d, "proj")
        os.makedirs(os.path.join(proj, "s1", "subagents"))
        write_log(os.path.join(proj, "s1.jsonl"), "claude-opus-5-5", [{"input_tokens": 0, "output_tokens": 100, "cache_read_input_tokens": 10000}])
        sub = os.path.join(proj, "s1", "subagents")
        for name, typ, desc, model in [
            ("agent-a", "dev", DESC_TASK1, "claude-haiku-5-5"),
            ("agent-b", "reviewer", DESC_TASK1_REVIEW, "claude-haiku-5-5"),
            ("agent-c", "dev", DESC_TASK2, "claude-sonnet-5-5"),
        ]:
            write_log(os.path.join(sub, name + ".jsonl"), model, [{"output_tokens": 1000}])
            with open(os.path.join(sub, name + ".meta.json"), "w", encoding="utf-8") as f:
                json.dump({"agentType": typ, "description": desc}, f)
        self.tasks = os.path.join(self.d, "TASKS.md")
        Path(self.tasks).write_text("- [x] task-1 — x\n- [ ] task-2 — y\n", encoding="utf-8")

    def test_task_key(self):
        self.assertEqual(ledger.task_key(DESC_TASK1), "stage1/" + TASK1)
        self.assertEqual(ledger.task_key("Task-3 fix"), TASK3)
        self.assertIsNone(ledger.task_key("plan round 2"))

    def test_weights(self):
        self.assertEqual(ledger.weight("claude-haiku-5-5", ledger.WEIGHTS), 1.0)
        self.assertEqual(ledger.weight("claude-opus-5-5", ledger.WEIGHTS), 5.0)

    def test_build(self):
        r = ledger.build([self.d], ledger.WEIGHTS, self.tasks)
        # lead: opus, 5*(100*5 + 10000*0.1)/1000 = 7.5 ; haiku sub: 1*(1000*5)/1000 = 5 each ; sonnet sub: 15
        self.assertAlmostEqual(r["lead_units"], 7.5)
        self.assertAlmostEqual(r["sub_units"], 25.0)
        self.assertEqual(r["by_task"]["stage1/" + TASK1]["runs"], 2)
        self.assertEqual(r["by_type"]["dev"]["runs"], 2)
        self.assertEqual(r["done"], {TASK1})

    def test_render(self):
        text = ledger.render(ledger.build([self.d], ledger.WEIGHTS, self.tasks), 5)
        self.assertIn("Cost per completed task", text)
        self.assertIn("1 done", text)

    def test_done_tasks_reads_and_closes(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            self.assertEqual(ledger.done_tasks(self.tasks), {TASK1})
            import gc

            gc.collect()
        self.assertEqual(ledger.done_tasks(None), set())

    def test_usd_uses_price_table_and_flags_unknown(self):
        r = ledger.build([self.d], ledger.WEIGHTS, self.tasks)
        # opus lead: 100 out * $20/M + 10000 cr * $0.2/M = 0.004 ; haiku 2 x 1000 out * $0.5/M ; sonnet 1000 out * $10/M
        self.assertAlmostEqual(r["usd"], 0.004 + 0.001 + 0.01, places=6)
        self.assertEqual(r["estimated"], set())
        p = os.path.join(self.d, "x.jsonl")
        write_log(p, "mystery-9", [{"output_tokens": 1000}])
        r2 = ledger.read_log(p, ledger.WEIGHTS)
        self.assertEqual(r2["estimated"], {"mystery-9"})
        self.assertGreater(r2["usd"], 0)
        self.assertIn("ESTIMATE", ledger.render({**r, "estimated": {"mystery-9"}}, 3))


class HeadlessLogsTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": HEADLESS_FIXTURE})
        env.start()
        self.addCleanup(env.stop)
        self.projects = os.path.join(HEADLESS_FIXTURE, "projects")
        self.sids = ledger.metrics_sids(os.path.join(HEADLESS_FIXTURE, "metrics.jsonl"))

    def test_find_transcript_searches_config_dir_root(self):
        path = ledger.find_transcript(HEADLESS_SID)
        assert path is not None
        self.assertEqual(os.path.basename(path), HEADLESS_SID + ".jsonl")
        self.assertEqual(os.path.dirname(os.path.dirname(path)), self.projects)

    def test_find_sessions_takes_headless_sid(self):
        leads = [os.path.basename(lead) for _sid, lead, _agents in ledger.find_sessions([self.projects], self.sids) if lead]
        self.assertEqual(leads, [HEADLESS_SID + ".jsonl"])

    def test_ledger_and_run_summary_agree_per_stage(self):
        built = ledger.build([self.projects], ledger.WEIGHTS, None, sids=self.sids)
        _sig, rows, _total = rn.analyse([self.projects], 0.0, self.sids)
        lead_rows = [r for kind, _desc, r in rows if kind == "lead"]
        ledger_sessions = [s for s in ledger.find_sessions([self.projects], self.sids) if s[1]]
        self.assertEqual(len(ledger_sessions), len(lead_rows))
        self.assertEqual(built["lead_turns"], sum(r["turns"] for r in lead_rows))
        self.assertAlmostEqual(built["lead_units"], sum(r["units"] for r in lead_rows))
        self.assertEqual((built["lead_turns"], len(ledger_sessions)), (2, 1))


class DedupeTest(unittest.TestCase):
    def test_records_of_one_message_count_once(self):
        d = tracked_mkdtemp()
        p = os.path.join(d, "a.jsonl")
        with open(p, "w") as f:
            for _blk, out in (("thinking", 10), ("tool_use", 10), ("tool_use", 300)):  # 3 records, ONE message; the last has the final output count
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {"id": "m1", "model": "claude-haiku-5-5", "usage": {"output_tokens": out, "cache_read_input_tokens": 1000}},
                        }
                    )
                    + "\n"
                )
            f.write(
                json.dumps(
                    {"type": "assistant", "message": {"id": "m2", "model": "claude-haiku-5-5", "usage": {"output_tokens": 50, "cache_read_input_tokens": 1500}}}
                )
                + "\n"
            )
        r = ledger.read_log(p, ledger.WEIGHTS)
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["tokens"]["output"], 350)
        self.assertEqual(r["tokens"]["cache_read"], 2500)


if __name__ == "__main__":
    unittest.main()
