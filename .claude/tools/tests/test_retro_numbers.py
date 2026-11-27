import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import retro_numbers as rn
from tests.support.tmpdirs import tracked_mkdtemp

KEY1 = "task-" + str(1)  # task key as the ledger parses it
KEY4 = "task-" + str(4)


def log(path, turns, ctx, model="claude-haiku-4-5", ts="2026-10-27T10:00:00Z"):
    with open(path, "w") as f:
        for i in range(turns):
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": ts,
                        "message": {
                            "id": f"m{i}",
                            "model": model,
                            "usage": {"input_tokens": 10, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 0, "output_tokens": 50},
                        },
                    }
                )
                + "\n"
            )


class RetroNumbersTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.sub = f"{self.d}/p/sess/subagents"
        os.makedirs(self.sub)
        self.n = 0

    def agent(self, kind, desc, turns, ctx, model="claude-haiku-4-5", ts="2026-10-27T10:00:00Z"):
        self.n += 1
        log(f"{self.sub}/agent-{self.n}.jsonl", turns, ctx, model, ts)
        with open(f"{self.sub}/agent-{self.n}.meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": kind, "description": desc}, fh)

    def run_it(self, since=0.0):
        return rn.analyse([self.d], since)[0]

    def test_quiet_stage_trips_nothing(self):
        self.agent("dev", "s task-1", 10, 20_000)
        self.agent("reviewer", "s task-1 review", 8, 20_000)
        self.assertEqual(self.run_it(), [])

    def test_context_and_turn_caps_per_agent_kind(self):
        self.agent("dev", "s " + KEY1, 41, 30_000)  # dev cap 40
        self.agent("sec-auditor", "s audit", 90, 120_000)  # auditor cap 100 ok, but context > 100k
        sig = {n + str(i): t for i, (n, t) in enumerate(self.run_it())}
        text = " | ".join(sig.values())
        self.assertIn("dev 's " + KEY1 + "' 41 turns (> 40)", text)
        self.assertIn("sec-auditor 's audit' peak context 120k", text)
        self.assertNotIn("sec-auditor 's audit' 90 turns", text)

    def test_cost_shape_audit_share_and_expensive_task(self):
        for i in range(1, 4):
            self.agent("dev", f"s task-{i}", 5, 10_000)
        self.agent("dev", "s " + KEY4, 5, 10_000, model="claude-opus-4-7")  # weight 5x vs haiku 1x -> over 3x the median
        self.agent("arch-auditor", "s audit", 5, 400_000, model="claude-opus-4-7")
        text = " | ".join(t for _, t in self.run_it())
        self.assertIn("audits + architect =", text)
        self.assertIn(KEY4 + " costs", text)

    def test_since_skips_older_logs(self):
        self.agent("dev", "s old", 60, 30_000, ts="2026-10-26T10:00:00Z")
        self.assertEqual(self.run_it(rn.epoch("2026-10-27T00:00:00Z")), [])

    def test_render_when_nothing_tripped(self):
        self.agent("dev", "s task-1", 3, 5_000)
        self.assertIn("RETRO-NUMBERS: nothing tripped", rn.render(*rn.analyse([self.d])))

    def test_repeated_explore_lines_collapse_to_one_with_count(self):
        sig = [("explore", "lead session a grep"), ("big-reads", "x"), ("explore", "dev b grep"), ("explore", "dev c grep")]
        lines = [x for x in rn.render(sig, [], 0.0).splitlines() if x.startswith("RETRO explore")]
        self.assertEqual(lines, ["RETRO explore: 3 sessions (first: lead session a grep)"])
        self.assertIn("RETRO big-reads: x", rn.render(sig, [], 0.0))


class ExploreSignalsTest(unittest.TestCase):
    def scan(self, blocks_by_msg):
        d = tracked_mkdtemp()
        path = f"{d}/a.jsonl"
        with open(path, "w") as f:
            for i, blocks in enumerate(blocks_by_msg):
                kind = "assistant" if i % 2 == 0 else "user"
                f.write(
                    json.dumps({"type": kind, "timestamp": "2026-10-27T10:00:00Z", "message": {"id": f"m{i}", "content": blocks, "usage": {"input_tokens": 1}}})
                    + "\n"
                )
        return rn.scan(path)

    def test_big_reads_and_grep(self):
        msgs = []
        for i in range(3):
            msgs.append([{"type": "tool_use", "id": f"r{i}", "name": "Read", "input": {"file_path": f"/x/f{i}.js"}}])
            msgs.append([{"type": "tool_result", "tool_use_id": f"r{i}", "content": "x" * 31000}])
        for i in range(10):
            msgs.append([{"type": "tool_use", "id": f"g{i}", "name": "Bash", "input": {"command": "grep -rn foo src"}}])
            msgs.append([])
        r = self.scan(msgs)
        names = [n for n, _ in rn.explore_signals("lead", r)]
        self.assertEqual(sorted(names), ["big-reads", "explore"])

    def test_limited_read_and_local_py_ok(self):
        msgs = [
            [{"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "/x", "limit": 50}}],
            [{"type": "tool_result", "tool_use_id": "r", "content": "x" * 40000}],
        ]
        for i in range(10):
            msgs += [[{"type": "tool_use", "id": f"n{i}", "name": "Bash", "input": {"command": "python3 -I local.py nav def X"}}], []]
        self.assertEqual(rn.explore_signals("lead", self.scan(msgs)), [])

    def test_grep_tool_counts(self):
        msgs = []
        for i in range(4):
            msgs += [[{"type": "tool_use", "id": f"g{i}", "name": "Grep", "input": {"pattern": "x"}}], []]
        self.assertEqual(self.scan(msgs)["grep"], 4)


def row(kind, grep, nav, off=0):
    return (kind, "", {"grep": grep, "nav": nav, "offload": off})


class ExploreRatioTest(unittest.TestCase):
    def test_trips_below_share_with_per_role(self):
        sig = rn.explore_ratio([row("dev", 9, 1), row("lead", 3, 0)])
        self.assertEqual(len(sig), 1)
        self.assertIn("dev 1/10 (10%)", sig[0][1])
        self.assertIn("lead 0/3 (0%)", sig[0][1])

    def test_quiet_when_share_ok_or_few_calls(self):
        self.assertEqual(rn.explore_ratio([row("dev", 5, 5)]), [])
        self.assertEqual(rn.explore_ratio([row("dev", 5, 1)]), [])


class LeadSinceTest(unittest.TestCase):
    def test_old_lead_transcript_skipped(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/p/sess/subagents")
        log(f"{d}/p/sess.jsonl", 60, 200_000, ts="2026-10-26T10:00:00Z")
        self.assertTrue(rn.analyse([d])[0])
        self.assertEqual(rn.analyse([d], rn.epoch("2026-10-27T00:00:00Z"))[0], [])
        self.assertEqual(rn.analyse([d], rn.epoch("2026-10-27T00:00:00Z"))[1], [])

    def test_old_lead_keeps_its_new_subagents(self):
        d = tracked_mkdtemp()
        sub = f"{d}/p/sess/subagents"
        os.makedirs(sub)
        log(f"{d}/p/sess.jsonl", 3, 1_000, ts="2026-10-26T10:00:00Z")  # lead started before the stage
        log(f"{sub}/agent-1.jsonl", 60, 30_000, ts="2026-10-27T11:00:00Z")
        with open(f"{sub}/agent-1.meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": "dev", "description": "s new"}, fh)
        rows = rn.analyse([d], rn.epoch("2026-10-27T00:00:00Z"))[1]
        self.assertEqual([r[0] for r in rows], ["dev"])

    def test_wave_breakdown(self):
        d = tracked_mkdtemp()
        m = f"{d}/metrics.jsonl"
        rows = [
            {"ev": "session", "wave": 1, "agent": "dev", "cost_usd": 1.0, "tools": {"Read": 3, "Bash": 1}},
            {"ev": "session", "wave": 1, "agent": "reviewer", "cost_usd": 0.5, "tools": {"Read": 2}},
            {"ev": "session", "wave": 2, "agent": "dev", "cost_usd": 2.0, "tools": {"Edit": 4}},
            {"ev": "session", "wave": 2, "agent": "dev", "cost_usd": 1.0},
            {"ev": "wave", "wave": 2},
        ]
        Path(m).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        o = "\n".join(rn.wave_breakdown(m))
        self.assertIn("wave 1: 2 session(s), $1.50, 0 with no tool data", o)
        self.assertIn("Read", o)
        self.assertIn("83%", o)
        self.assertIn("$1.25", o)  # Read 5 of 6 calls; 0.75+0.5
        self.assertIn("role dev: Read 3, Bash 1", o)
        self.assertIn("wave 2: 2 session(s), $3.00, 1 with no tool data", o)
        self.assertEqual(rn.wave_breakdown(f"{d}/none.jsonl"), [])


class MetricsSignalsTest(unittest.TestCase):
    def mk(self, sessions):
        p = os.path.join(tracked_mkdtemp(), "metrics.jsonl")
        with open(p, "w") as f:
            for w, c, tools in sessions:
                f.write(json.dumps({"ev": "session", "wave": w, "cost_usd": c, "tools": tools}) + "\n")
        return p

    def test_wave_cost_tripped(self):
        p = self.mk([("w1", 1, {"Read": 1}), ("w2", 1, {"Read": 1}), ("w3", 5, {"Read": 1})])
        sig = rn.metrics_signals(p)
        self.assertEqual([n for n, _ in sig], ["wave-cost"])
        self.assertIn("w3", sig[0][1])

    def test_script_and_waveless_sessions_do_not_set_the_median(self):
        p = os.path.join(tracked_mkdtemp(), "metrics.jsonl")
        rows = [("w1", 1, "dev"), ("w2", 1, "dev"), ("w3", 5, "dev"), ("w4", 0, "script"), ("w5", 0, "script"), ("w6", 0, "script"), (None, 0.5, "dev")]
        with open(p, "w") as f:
            for w, c, a in rows:
                f.write(json.dumps({"ev": "session", "wave": w, "cost_usd": c, "agent": a, "tools": {"Read": 1}}) + "\n")
        sig = rn.metrics_signals(p)
        self.assertIn("the median wave ($1.00)", sig[0][1])

    def test_cheap_waves_are_never_flagged(self):
        p = self.mk([("w1", 0.01, {"Read": 1}), ("w2", 0.01, {"Read": 1}), ("w3", 0.15, {"Read": 1})])
        self.assertEqual(rn.metrics_signals(p), [])

    def test_wave_cost_not_tripped(self):
        p = self.mk([("w1", 1, {"Read": 1}), ("w2", 1.5, {"Read": 1}), ("w3", 2, {"Read": 1})])
        self.assertEqual(rn.metrics_signals(p), [])

    def test_wave_cost_needs_three_waves(self):
        p = self.mk([("w1", 1, {"Read": 1}), ("w2", 9, {"Read": 1})])
        self.assertEqual(rn.metrics_signals(p), [])

    def test_tools_missing_tripped(self):
        p = self.mk([("w1", 1, {}), ("w1", 1, None), ("w1", 1, {"Read": 1})])
        self.assertEqual(rn.metrics_signals(p), [("tools-missing", "2 of 3 sessions without tool data")])

    def test_tools_missing_not_tripped(self):
        p = self.mk([("w1", 1, {}), ("w1", 1, {"Read": 1})])
        self.assertEqual(rn.metrics_signals(p), [])

    def test_script_steps_are_not_counted_as_missing_tool_data(self):
        p = os.path.join(tracked_mkdtemp(), "metrics.jsonl")
        with open(p, "w") as f:
            for agent, tools in [("script", None)] * 5 + [("dev", {"Read": 1}), ("dev", None)]:
                f.write(json.dumps({"ev": "session", "wave": "w1", "cost_usd": 0, "agent": agent, "tools": tools}) + "\n")
        self.assertEqual(rn.metrics_signals(p), [])  # 1 of 2 model sessions, not 6 of 7

    def test_missing_file(self):
        self.assertEqual(rn.metrics_signals("/nonexistent/metrics.jsonl"), [])
        self.assertEqual(rn.metrics_signals(""), [])


class ContextSignalsTest(unittest.TestCase):
    def sig(self, *events):
        import json

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        self.addCleanup(os.unlink, f.name)
        return dict(rn.metrics_signals(f.name))

    OK = {"ev": "context", "code_files": 25, "secs_total": 170}

    def test_healthy_context_trips_nothing(self):
        self.assertEqual(self.sig(self.OK, {"ev": "context-recall", "hits": 8, "total": 10, "missed": []}), {})

    def test_skipped_is_reported_not_silent(self):
        self.assertIn("context-skipped", self.sig({**self.OK, "code_files": 0}))

    def test_slow(self):
        self.assertIn("context-slow", self.sig({**self.OK, "secs_total": 301}))

    def test_explore_loop_cut_short_trips(self):
        self.assertIn("context-explore-cut", self.sig({**self.OK, "explore_ended": "budget", "explore_turns": 25}))
        self.assertIn("context-explore-cut", self.sig({**self.OK, "explore_ended": "error:TimeoutError"}))
        self.assertNotIn("context-explore-cut", self.sig({**self.OK, "explore_ended": "answer"}))
        self.assertNotIn("context-explore-cut", self.sig(self.OK))

    def test_live_recall_low_trips_only_with_enough_files(self):
        self.assertIn("context-recall", self.sig({"ev": "context-recall", "hits": 1, "total": 6, "missed": ["a.py"]}))
        self.assertEqual(self.sig({"ev": "context-recall", "hits": 0, "total": 3, "missed": ["a.py"]}), {})


class PrereviewLinesTest(unittest.TestCase):
    def test_counts_caught_and_missed(self):
        def ev(**k):
            return json.dumps(k)

        rows = []
        for tid, local, rev in (
            ("task-1", "FAIL", "PASS"),
            ("task-2", "FAIL", "FAIL"),
            ("task-3", "DONE", "PASS"),
            ("task-4", "DONE", "FAIL"),
            ("task-5", "DONE", None),
        ):
            rows += [
                ev(ev="dispatch", id=f"p{tid}", task=tid, role="script", why="local pre-review (lp review)"),
                ev(ev="report", id=f"p{tid}", task=tid, role="script", verdict=local),
            ]
            if rev:
                rows.append(ev(ev="report", id=f"r{tid}", task=tid, role="reviewer", verdict=rev))
        p = os.path.join(tracked_mkdtemp(), "orch.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(rows) + "\n")
        line = rn.prereview_lines(p)[0]
        self.assertIn("5 gated", line)
        self.assertIn("local FAIL 2 (paid reviewer then PASS 1, FAIL 1", line)
        self.assertIn("local pass 3 (reviewer PASS 1, FAIL 1 = missed by the gate, not yet 1)", line)
        self.assertEqual(rn.prereview_lines(os.path.join(tracked_mkdtemp(), "none.jsonl")), [])

    def test_skipped_report_counts_as_skipped_not_gated(self):
        def ev(**k):
            return json.dumps(k)

        rows = [
            ev(ev="dispatch", id="p1", task="task-1", role="script", why="local pre-review (lp review)"),
            ev(ev="report", id="p1", task="task-1", role="script", verdict="SKIPPED"),
            ev(ev="dispatch", id="p2", task="task-2", role="script", why="local pre-review (lp review)"),
            ev(ev="report", id="p2", task="task-2", role="script", verdict="FAIL"),
            ev(ev="report", id="r2", task="task-2", role="reviewer", verdict="PASS"),
        ]
        p = os.path.join(tracked_mkdtemp(), "orch.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(rows) + "\n")
        line = rn.prereview_lines(p)[0]
        self.assertIn("1 gated", line)
        self.assertIn("local FAIL 1 (paid reviewer then PASS 1, FAIL 0, not yet 0)", line)
        self.assertIn("local pass 0 (reviewer PASS 0, FAIL 0 = missed by the gate, not yet 0)", line)
        self.assertIn("1 skipped: no diff / too big / error", line)


class ResolvedLinesTest(unittest.TestCase):
    def test_lists_resolved_events_only(self):
        p = os.path.join(tracked_mkdtemp(), "orch.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"ev": "report", "task": "t"}) + "\n")
            f.write(json.dumps({"ev": "resolved", "task": "merge-fix-w1", "line": "RESOLVED: a.js: dev: other side contained"}) + "\n")
        out = rn.resolved_lines(p)
        self.assertEqual(len(out), 2)
        self.assertIn("1 file(s)", out[0])
        self.assertIn("merge-fix-w1: RESOLVED: a.js", out[1])
        self.assertEqual(rn.resolved_lines(p + ".none"), [])


if __name__ == "__main__":
    unittest.main()


class GateLines(unittest.TestCase):
    def test_counts_vetoes_go_ons_and_consequences(self):
        d = tracked_mkdtemp()

        def ev(**k):
            return json.dumps(k)

        def gate(tid, verdict, text):
            f = os.path.join(d, f"g{tid}.md")
            with open(f, "w") as fh:
                fh.write(f"VERDICT: {verdict}\n{text}\n")
            return [
                ev(ev="dispatch", id=f"g{tid}", task=tid, role="script", why="escalation gate (lp judge)"),
                ev(ev="report", id=f"g{tid}", task=tid, role="script", verdict=verdict, file=f),
            ]

        sonnet = lambda t: [ev(ev="dispatch", id=f"s{t}", task=t, role="dev", model="sonnet"), ev(ev="report", id=f"s{t}", task=t, role="dev", verdict="DONE")]  # noqa: E731
        passed = lambda t: [ev(ev="report", id=f"p{t}", task=t, role="reviewer", verdict="PASS")]  # noqa: E731
        rows = [
            *gate("t1", "FAIL", "judge FAIL: stale file | BLOCKED.md"),
            ev(ev="retry", task="t1"),  # veto, lead retried
            *gate("t2", "FAIL", "judge FAIL: denied write"),
            ev(ev="retry", task="t2"),
            *passed("t2"),  # veto, retried, then passed: maybe a false veto
            *gate("t3", "FAIL", "judge FAIL: no input"),  # veto, nothing yet
            *gate("t4", "DONE", "judge PASS: code defect"),
            *sonnet("t4"),
            *passed("t4"),  # go-on, Sonnet fixed it
            *gate("t5", "DONE", "judge PASS: code defect"),
            *sonnet("t5"),  # go-on, Sonnet did not fix it
            *gate("t6", "DONE", "gate skipped: local model unreachable or no usable answer"),
        ]
        p = os.path.join(d, "orch.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(rows) + "\n")
        lines = rn.gate_lines(p)
        self.assertIn("6 calls", lines[0])
        self.assertIn("vetoes 3 (lead retry 1, retry then PASS 1, skip 0+0, open 1)", lines[0])
        self.assertIn("go-on 2 (Sonnet fixed 1, Sonnet did not fix 1, open 0)", lines[0])
        self.assertIn("no answer 1", lines[0])
        self.assertTrue(any(line.startswith("gate veto t2 gt2: judge FAIL: denied write") and line.endswith("-> retry+pass") for line in lines))
        self.assertEqual(rn.gate_lines(os.path.join(d, "none.jsonl")), [])
