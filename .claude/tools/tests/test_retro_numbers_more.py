"""Characterization tests for retro_numbers: scan edge cases, stage_start, main, junk lines in event files, wave_breakdown details."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import retro_numbers as rn
from tests.support.tmpdirs import tracked_mkdtemp


def use(i, name, inp):
    return {
        "type": "assistant",
        "message": {
            "id": f"m{i}",
            "usage": {"input_tokens": 5, "output_tokens": 1},
            "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}],
        },
    }


def result(i, content):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": content}]}}


class Tmp(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name

    def write(self, name, lines):
        p = os.path.join(self.d, name)
        Path(p).write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")
        return p


class Scan(Tmp):
    def test_junk_tools_and_sizes(self):
        big = "x" * 40_000
        p = self.write(
            "t.jsonl",
            [
                "not json",
                "[1, 2]",
                {"timestamp": "2026-10-27T10:00:00Z", "message": "plain string"},
                {"message": {"content": ["str", {"type": "other"}]}},
                use(1, "Read", {"file_path": "/a.py"}),
                result(1, big),
                use(2, "Read", {"file_path": "/b.py", "limit": 10}),
                result(2, big),
                use(3, "Read", {"file_path": "/c.py"}),
                result(3, [{"text": big}, "junk", {"text": None}]),
                use(4, "Read", {"file_path": "/d.py"}),
                result(4, 5),
                use(5, "Bash", {"command": "lp find where is x"}),
                use(6, "Bash", {"command": "python3 local.py judge a b"}),
                use(7, "Bash", {"command": "cat f | grep foo"}),
                use(8, "Bash", {"command": 5}),
                use(9, "Grep", "oops"),
                result(10, "orphan"),
            ],
        )
        r = rn.scan(p)
        self.assertEqual(r["big_reads"], [("/a.py", 40000), ("/c.py", 40000)])
        self.assertEqual((r["grep"], r["nav"], r["offload"]), (2, 1, 1))
        self.assertEqual(r["first"], rn.epoch("2026-10-27T10:00:00Z"))
        self.assertEqual(r["peak_ctx"], 5)

    def test_epoch_bad(self):
        self.assertEqual(rn.epoch("garbage"), 0.0)
        self.assertEqual(rn.epoch(None), 0.0)


class StageStart(Tmp):
    def test_stage_start(self):
        self.assertEqual(rn.stage_start(""), 0.0)
        self.assertEqual(rn.stage_start(os.path.join(self.d, "none.jsonl")), 0.0)
        p = self.write("o.jsonl", [{"t": 50.0}, "", {"ev": "x"}, {"t": 20.0}])
        self.assertEqual(rn.stage_start(p), 20.0)
        self.assertEqual(rn.stage_start(self.write("e.jsonl", [{"ev": "x"}])), 0.0)

    def test_lines_that_are_not_objects_are_skipped(self):
        p = os.path.join(self.d, "mixed.jsonl")
        Path(p).write_text('[1, 2]\n"text"\nnot json\n{"t": 7.0}\n', encoding="utf-8")
        self.assertEqual(rn.stage_start(p), 7.0)


class EventFiles(Tmp):
    def test_missing_files(self):
        for fn in (rn.prereview_lines,):
            self.assertEqual(fn(""), [])
            self.assertEqual(fn(os.path.join(self.d, "none")), [])
        self.assertEqual(rn.wave_breakdown(""), [])
        self.assertEqual(rn.metrics_signals(os.path.join(self.d, "none")), [])

    def test_junk_lines_skipped(self):
        evs = [
            "garbage",
            {"ev": "dispatch", "role": "script", "why": "other script", "id": "a", "task": "t1"},
            {"ev": "dispatch", "role": "script", "why": None, "id": "b", "task": "t2"},
        ]
        p = self.write("o.jsonl", evs)
        self.assertEqual(rn.prereview_lines(p), [])

    def test_prereview_open_and_reviewer_fail(self):
        evs = [
            {"ev": "dispatch", "role": "script", "why": "local pre-review", "id": "a", "task": "t1"},
            {"ev": "dispatch", "role": "script", "why": "local pre-review", "id": "b", "task": "t2"},
            {"ev": "dispatch", "role": "script", "why": "local pre-review", "id": "c", "task": "t3"},
            {"ev": "report", "id": "a", "verdict": "PASS"},
            {"ev": "report", "id": "b", "verdict": "FAIL"},
            {"ev": "report", "role": "reviewer", "task": "t1", "verdict": "FAIL"},
            {"ev": "report", "role": "reviewer", "task": "t2", "verdict": "PASS"},
        ]
        self.assertEqual(
            rn.prereview_lines(self.write("o.jsonl", evs)),
            [
                (
                    "pre-review (local gate): 3 gated; local FAIL 1 (paid reviewer then PASS 1, FAIL 0, not yet 0); "
                    "local pass 1 (reviewer PASS 0, FAIL 1 = missed by the gate, not yet 0); 0 skipped: no diff / too big / error"
                )
            ],
        )

    def test_prereview_skipped_not_counted_as_pass(self):
        evs = [
            {"ev": "dispatch", "role": "script", "why": "local pre-review", "id": "a", "task": "t1"},
            {"ev": "dispatch", "role": "script", "why": "local pre-review", "id": "b", "task": "t2"},
            {"ev": "report", "id": "a", "verdict": "SKIPPED"},
            {"ev": "report", "id": "b", "verdict": "DONE"},
            {"ev": "report", "role": "reviewer", "task": "t1", "verdict": "FAIL"},
            {"ev": "report", "role": "reviewer", "task": "t2", "verdict": "PASS"},
        ]
        self.assertEqual(
            rn.prereview_lines(self.write("o.jsonl", evs)),
            [
                (
                    "pre-review (local gate): 1 gated; local FAIL 0 (paid reviewer then PASS 0, FAIL 0, not yet 0); "
                    "local pass 1 (reviewer PASS 1, FAIL 0 = missed by the gate, not yet 0); 1 skipped: no diff / too big / error"
                )
            ],
        )

    def test_context_junk_and_unknown(self):
        p = self.write("m.jsonl", ["junk", "[1]", {"ev": "other"}, {"ev": "context-recall", "total": 5, "hits": 5}])
        self.assertEqual(rn.context_signals(p), [])

    def test_context_signals_silent_when_local_model_off(self):
        off = {"local_model": False}
        p = self.write(
            "m.jsonl",
            [
                {"ev": "context", "code_files": 0, "explore_ended": "error:URLError", **off},
                {"ev": "context-recall", "total": 8, "hits": 0, "missed": ["a.py"], **off},
            ],
        )
        self.assertEqual(rn.context_signals(p), [])
        on = self.write("m2.jsonl", [{"ev": "context", "code_files": 0, "local_model": True}])
        self.assertTrue(rn.context_signals(on))

    def test_wave_breakdown_details(self):
        recs = [
            "junk",
            {"ev": "session", "wave": "w1", "agent": "dev", "cost_usd": 1.0, "tools": {"Bash": 3, "Read": 1, "bad": "x", "zero": 0}},
            {"ev": "session", "wave": "w1", "agent": None, "cost_usd": 0.5, "tools": {"Read": 1}},
            {"ev": "session", "wave": "w1", "cost_usd": 0.25},
            {"ev": "session", "wave": "w2", "cost_usd": 0},
            {"ev": "other", "wave": "w3"},
        ]
        out = rn.wave_breakdown(self.write("m.jsonl", recs))
        self.assertEqual(
            out,
            [
                "-- wave w1: 3 session(s), $1.75, 1 with no tool data",
                "   tool            calls  share  est. cost (estimated, not measured per tool)",
                "   Bash                3   60%  $0.75",
                "   Read                2   40%  $0.75",
                "   role ?: Read 1",
                "   role dev: Bash 3, Read 1",
                "-- wave w2: 1 session(s), $0.00, 1 with no tool data",
            ],
        )


class Main(Tmp):
    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rn.main(list(argv))
        return buf.getvalue()

    def test_stdout_verdict_and_out_file(self):
        log = self.write("t.jsonl", [use(1, "Bash", {"command": "ls"})])
        out = self.run_main(log, "--verdict")
        self.assertTrue(out.startswith("VERDICT: DONE\nRETRO-NUMBERS: nothing tripped\n-- 1 transcript(s)"))
        orch = self.write("orch.jsonl", [{"t": 1.0}])
        self.write("metrics.jsonl", [{"ev": "session", "wave": "w1", "cost_usd": 1, "tools": {"Bash": 1}}])
        dest = os.path.join(self.d, "out.txt")
        msg = self.run_main(log, "--orch", orch, "--out", dest)
        self.assertEqual(msg, f"retro numbers written to {dest}\n")
        text = Path(dest).read_text()
        self.assertIn("-- wave w1: 1 session(s), $1.00, 0 with no tool data", text)


class ReviewRoundLinesTest(unittest.TestCase):
    def test_counts_rounds_to_first_pass_and_never_passed(self):
        evs = [
            {"ev": "report", "role": "reviewer", "task": "t1", "verdict": "PASS"},
            {"ev": "report", "role": "reviewer", "task": "t2", "verdict": "FAIL"},
            {"ev": "report", "role": "reviewer", "task": "t2", "verdict": "PASS"},
            {"ev": "report", "role": "reviewer", "task": "t2", "verdict": "FAIL"},  # after the pass: ignored
            {"ev": "report", "role": "reviewer", "task": "t3", "verdict": "FAIL"},
            {"ev": "report", "role": "dev", "task": "t4", "verdict": "DONE"},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "orch.jsonl")
            Path(path).write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")
            self.assertEqual(rn.review_round_lines(path), ["review rounds to first PASS over 3 task(s): 1: 1, 2: 1, 3+: 0; never passed: 1"])
            self.assertEqual(rn.review_round_lines(os.path.join(d, "none.jsonl")), [])


if __name__ == "__main__":
    unittest.main()


class DriverSessionsTest(unittest.TestCase):
    def test_top_level_stage_sessions_counted(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d, "projects", "p")
            proj.mkdir(parents=True)
            rec = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 5, "cache_read_input_tokens": 200000, "output_tokens": 1}}}
            (proj / "sidA.jsonl").write_text(json.dumps(rec) + "\n")
            (proj / "other.jsonl").write_text(json.dumps(rec) + "\n")
            m = Path(d, ".work", "S", "metrics.jsonl")
            m.parent.mkdir(parents=True)
            m.write_text(json.dumps({"ev": "session", "sid": "sidA"}) + "\n" + json.dumps({"ev": "session", "sid": ""}) + "\n")
            root = os.path.join(d, "projects")
            self.assertEqual(rn.analyse([root])[1], [])
            _, rows, _ = rn.analyse([root], 0.0, rn.ledger.metrics_sids(str(m)))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "lead")


class RecallLinesTest(unittest.TestCase):
    def test_line_for_good_and_bad_and_none(self):
        d = tracked_mkdtemp()
        p = os.path.join(d, "metrics.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"ev": "context-recall", "hits": 9, "total": 10, "missed": ["a.js"], "named": 12, "precision": 0.75, "mode": "explore"}) + "\n")
            f.write(json.dumps({"ev": "context-recall", "hits": 0, "total": 28, "missed": []}) + "\n")
            f.write(json.dumps({"ev": "context-recall", "hits": 0, "total": 0, "missed": []}) + "\n")
        lines = rn.recall_lines(p)
        self.assertEqual(len(lines), 2)
        self.assertIn("9 of 10 modified files named (90%", lines[0])
        self.assertIn("precision 75% of 12 named, mode explore", lines[0])
        self.assertNotIn("precision", lines[1])
        self.assertIn("0 of 28", lines[1])
        self.assertEqual(rn.recall_lines(os.path.join(d, "none.jsonl")), [])


class BlockedLines(unittest.TestCase):
    def test_classes_counted(self):
        import json
        import tempfile

        from retro import retro_numbers as rn

        rows = [("BLOCKED", "Bash requires approval"), ("BLOCKED", "task-3 not merged yet"), ("BLOCKED", "test fails"), ("DONE", "")]
        with tempfile.TemporaryDirectory() as d:
            p = d + "/orch.jsonl"
            with open(p, "w") as f:
                for v, r in rows:
                    f.write(json.dumps({"ev": "report", "verdict": v, "reason": r}) + "\n")
            self.assertEqual(rn.blocked_lines(p), ["BLOCKED reports 3: permission 1, precondition 1, defect 1"])
            self.assertEqual(rn.blocked_lines(d + "/none"), [])


class PhaseSizeLinesTest(unittest.TestCase):
    def test_lines_and_summary(self):
        d = tracked_mkdtemp()
        p = os.path.join(d, "metrics.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"ev": "phase-size", "phase": "p1", "estimate": 12, "actual": 7}) + "\n")
            f.write(json.dumps({"ev": "phase-size", "phase": "p2", "estimate": 5, "actual": 8}) + "\n")
        lines = rn.phase_size_lines(p)
        self.assertEqual(lines[0], "phase-size: p1 estimate 12 actual 7")
        self.assertEqual(lines[1], "phase-size: p2 estimate 5 actual 8")
        self.assertEqual(len(lines), 3)
        self.assertIn("2 phases, estimate vs actual: over 1, under 1, equal 0 (mean actual/estimate 1.09)", lines[2])
        self.assertEqual(rn.phase_size_lines(os.path.join(d, "none.jsonl")), [])

    def test_no_events_no_line(self):
        d = tracked_mkdtemp()
        p = os.path.join(d, "metrics.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"ev": "context-recall", "hits": 1, "total": 1}) + "\n")
        self.assertEqual(rn.phase_size_lines(p), [])

    def test_zero_estimate_excluded_from_ratio(self):
        d = tracked_mkdtemp()
        p = os.path.join(d, "metrics.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"ev": "phase-size", "phase": "p1", "estimate": 0, "actual": 3}) + "\n")
            f.write(json.dumps({"ev": "phase-size", "phase": "p2", "estimate": 4, "actual": 4}) + "\n")
        lines = rn.phase_size_lines(p)
        self.assertEqual(len(lines), 3)
        self.assertIn("over 0, under 0, equal 1 (mean actual/estimate 1.00)", lines[2])
        only_zero = os.path.join(d, "zero.jsonl")
        with open(only_zero, "w") as f:
            f.write(json.dumps({"ev": "phase-size", "phase": "p3", "estimate": 0, "actual": 2}) + "\n")
        self.assertIn("mean actual/estimate n/a)", rn.phase_size_lines(only_zero)[-1])
