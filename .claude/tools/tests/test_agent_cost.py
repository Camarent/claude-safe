import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("agent_cost", os.path.join(HERE, "retro", "agent_cost.py"))
assert spec is not None and spec.loader is not None
agent_cost = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_cost)


def asst(uses, inp=0, cr=0, cc=0, out=0):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "usage": {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc, "output_tokens": out},
            "content": [{"type": "tool_use", "id": i, "name": n, "input": inp_d} for i, n, inp_d in uses],
        },
    }


def res(tid, text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


class AgentCostTest(unittest.TestCase):
    def setUp(self):
        self.dir = tracked_mkdtemp()
        self.path = os.path.join(self.dir, "log.jsonl")
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
            json.dumps(asst([("a", "Read", {"file_path": "/x/f.md"})], inp=10, cr=100, cc=50, out=5)),
            res("a", "x" * 100),
            json.dumps(asst([("b", "Read", {"file_path": "/x/f.md"}), ("c", "Bash", {"command": "make all"})], inp=2, cr=400, cc=0, out=7)),
            res("b", "y" * 40),
            res("c", "z" * 5000),
            "not json at all",
            json.dumps(asst([("d", "Bash", {"command": "make all"})], inp=3, cr=900, cc=10, out=1)),
            res("d", "small"),
            json.dumps(asst([("e", "Bash", {"command": "cat big | head -5"})], inp=1, cr=5000, out=2)),
            res("e", "w" * 9000),
        ]
        lines = [line if isinstance(line, str) else json.dumps(line) for line in lines]
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.records, self.bad = agent_cost.load(self.path)

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def test_totals(self):
        _turns, tool_calls, results, _reads, _bashes, totals = agent_cost.analyse(self.records)
        self.assertEqual(self.bad, 1)
        self.assertEqual(totals["turns"], 4)
        self.assertEqual(totals["tool_calls"], 5)
        self.assertEqual(totals["output"], 5 + 7 + 1 + 2)
        self.assertEqual(totals["cache_creation"], 50 + 10)
        self.assertEqual(totals["cache_read"], 100 + 400 + 900 + 5000)
        self.assertEqual(totals["input"], 10 + 2 + 3 + 1)
        self.assertEqual(tool_calls["Bash"], 3)
        self.assertEqual(sum(r["chars"] for r in results), 100 + 40 + 5000 + 5 + 9000)

    def test_one_message_split_into_blocks_counts_once(self):
        # Claude Code writes one record per content block; all share a message id and usage.
        usage = {"input_tokens": 4, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20, "output_tokens": 9}
        recs = []
        for i, name in enumerate(("Read", "Bash", "Grep")):
            block = {"type": "tool_use", "id": f"t{i}", "name": name, "input": {}}
            recs.append({"type": "assistant", "message": {"id": "msg_1", "role": "assistant", "usage": usage, "content": [block]}})
        turns, tool_calls, _results, _reads, _bashes, totals = agent_cost.analyse(recs)
        self.assertEqual(totals["turns"], 1)
        self.assertEqual(len(turns), 1)
        self.assertEqual(totals["input"], 4)
        self.assertEqual(totals["cache_read"], 100)
        self.assertEqual(totals["cache_creation"], 20)
        self.assertEqual(totals["output"], 9)
        self.assertEqual(totals["tool_calls"], 3)
        self.assertEqual(sum(tool_calls.values()), 3)

    def test_repeated_read_detected(self):
        _, _, _, reads, bashes, _ = agent_cost.analyse(self.records)
        self.assertEqual(reads["/x/f.md"], 2)
        self.assertEqual(bashes["make all"], 2)
        self.assertNotIn("cat big | head -5", [k for k, v in bashes.items() if v > 1])

    def test_unlimited_bash_detected(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print(agent_cost.report(self.path, 8))
        text = out.getvalue()
        section = text.split("5. Bash results")[1].split("6. Top")[0]
        self.assertIn("make all", section)
        self.assertIn("5,000", section)
        self.assertNotIn("cat big", section)
        self.assertIn("Read x2", text)

    def _report_with_turns(self, n):
        path = os.path.join(self.dir, f"turns{n}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(n):
                f.write(json.dumps(asst([], inp=1, out=1)) + "\n")
        out = io.StringIO()
        with redirect_stdout(out):
            print(agent_cost.report(path, 3))
        return out.getvalue()

    def test_retro_flag_above_turn_limit(self):
        self.assertIn("RETRO: 31 turns", self._report_with_turns(31))

    def test_no_retro_flag_at_turn_limit(self):
        self.assertNotIn("RETRO", self._report_with_turns(30))

    def test_missing_fields_do_not_crash(self):
        path = os.path.join(self.dir, "odd.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant"}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "text"}}) + "\n")
            f.write("[1, 2]\n")
        out = io.StringIO()
        with redirect_stdout(out):
            agent_cost.main([path, "--top", "3"])
        self.assertIn("assistant turns 1", out.getvalue())


class MetricsAndPricesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tracked_mkdtemp()

    def _metrics(self):
        path = os.path.join(self.dir, "metrics.jsonl")
        base = {"ev": "session", "turns": 3, "input": 1000000, "output": 100000, "cache_read": 0, "cache_write": 0}
        rows = [
            {**base, "model": "claude-opus-5-5", "agent": "dev", "cost_usd": 6.5, "sid": "a"},
            {**base, "model": "claude-opus-5-5", "agent": "dev", "cost_usd": 6.5, "sid": "b"},
            {**base, "model": "mystery-9", "agent": "scout", "cost_usd": 1.0, "sid": "c"},
            {"ev": "wave", "sessions": 3},
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        return path

    def test_metrics_file_aggregates_per_model_agent(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(agent_cost.main([self._metrics()]), 0)
        text = out.getvalue()
        self.assertIn("3 sessions", text)
        self.assertIn("claude-opus-5-5", text)
        self.assertIn("$   13.00", text)  # recorded, two opus sessions
        self.assertIn("$   12.00", text)  # table: 2 x (1M * $4 + 0.1M * $20)
        self.assertIn("ESTIMATE: no price row for mystery-9", text)

    def test_transcript_cost_by_model(self):
        path = os.path.join(self.dir, "t.jsonl")
        rec = asst([], inp=1000000, out=100000)
        rec["message"]["model"] = "claude-sonnet-5-5"
        rec["message"]["id"] = "m1"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")  # same id twice: counted once
        out = io.StringIO()
        with redirect_stdout(out):
            agent_cost.main([path])
        self.assertIn("claude-sonnet-5-5", out.getvalue())
        self.assertIn("$    3.00", out.getvalue())  # 1M * $2 + 0.1M * $10

    def test_transcript_cost_survives_bad_lines_and_unreadable_files(self):
        path = os.path.join(self.dir, "bad.jsonl")
        rec = asst([], inp=1000000, out=0)
        rec["message"]["model"] = "claude-sonnet-5-5"
        rec["message"]["id"] = "m1"
        odd = asst([], inp=1, out=1)
        odd["message"]["id"] = "m2"
        odd["message"]["usage"]["input_tokens"] = "lots"
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json\n[1]\n" + json.dumps(rec) + "\n" + json.dumps(odd) + "\n")
        got = agent_cost.transcript_cost(path)
        self.assertEqual(got["input"], 1000000)
        self.assertEqual(got["bad_lines"], 2)
        for unreadable in (os.path.join(self.dir, "missing.jsonl"), self.dir):
            got = agent_cost.transcript_cost(unreadable)
            self.assertEqual((got["input"], got["usd"], got["bad_lines"]), (0, 0.0, 0))

    def test_sid_lookup(self):
        home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(home, ".claude", "projects", "p"))
        target = os.path.join(home, ".claude", "projects", "p", "abc-123.jsonl")
        with open(target, "w", encoding="utf-8") as f:
            f.write("{}\n")
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            self.assertEqual(agent_cost.find_transcript("abc-123"), target)
            self.assertIsNone(agent_cost.find_transcript("nope"))
            self.assertIsNone(agent_cost.find_transcript("../x"))
            self.assertIsNone(agent_cost.find_transcript(""))
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old

    def test_main_needs_log_or_sid(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(agent_cost.main([]), 2)
        self.assertIn("--sid", err.getvalue())


if __name__ == "__main__":
    unittest.main()
