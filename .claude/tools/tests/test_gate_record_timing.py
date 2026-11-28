"""Gate time warnings: gate_timing.warn and check over real gate-run records, and the warn lines of log_gate_cmds (rc never changes)."""

import json
import os
import sys
import tempfile
import unittest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
from audit import final_check  # noqa: E402
from pipeline import gate_record as G  # noqa: E402
from pipeline import gate_timing as T  # noqa: E402
from pipeline.configuration import GATE_KIND_FULL, GATE_KIND_MERGE, GATE_KIND_TASK  # noqa: E402
from test_gate_record import make_repo  # noqa: E402

LIMITS = {"unit": 120, "merge": 300, "full": 540}


class TestWarn(unittest.TestCase):
    def test_over_limit_gives_line(self):
        line = T.warn("unit", 200, LIMITS)
        self.assertIn("unit gate run 200s over the 120s limit", line)

    def test_within_limit_gives_nothing(self):
        self.assertEqual(T.warn("unit", 120, LIMITS), "")
        self.assertEqual(T.warn("unit", 3.5, LIMITS), "")

    def test_no_secs_or_no_limit_gives_nothing(self):
        self.assertEqual(T.warn("unit", None, LIMITS), "")
        self.assertEqual(T.warn("nope", 999, LIMITS), "")

    def test_limit_zero_warns_on_any_run(self):
        # mutation check: a zero limit makes every timed run a warning
        self.assertTrue(T.warn("unit", 1, {"unit": 0}))


class TestFullKind(unittest.TestCase):
    def test_full_kind_is_level_full(self):
        self.assertEqual(T.LEVEL_OF_KIND[GATE_KIND_FULL], "full")

    def test_full_run_warns_against_full_limit(self):
        self.assertEqual(T.warn(T.LEVEL_OF_KIND[GATE_KIND_FULL], 600, LIMITS), "gate time: full gate run 600s over the 540s limit (gate_time_warn_s.full)")


class TestCheckRealRecords(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = make_repo(os.path.join(tmp.name, "ws"), {"a.txt": "x\n"})

    def test_recorded_slow_run_warns_fast_run_does_not(self):
        # level values are the ones qrun writes (parallel, serial); kind is the gate kind set by the caller (PIPELINE_GATE_KIND)
        G.record(self.repo, ["scripts/dev.sh", "test"], 0, secs=200.0, level="parallel", kind=GATE_KIND_TASK)
        G.record(self.repo, ["scripts/dev.sh", "check"], 0, secs=10.0, level="serial", kind=GATE_KIND_TASK)
        lines = T.check(self.repo, "unit")
        self.assertEqual(len(lines), 1)
        self.assertIn("200s", lines[0])

    def test_within_merge_limit_gives_none(self):
        G.record(self.repo, ["scripts/dev.sh", "test"], 0, secs=200.0, level="parallel", kind=GATE_KIND_MERGE)
        self.assertEqual(T.check(self.repo, "merge"), [])

    def test_merge_run_over_merge_limit_is_not_labelled_unit(self):
        G.record(self.repo, ["scripts/dev.sh", "test"], 0, secs=400.0, level="parallel", kind=GATE_KIND_MERGE)
        self.assertEqual(T.check(self.repo, "unit"), [])
        lines = T.check(self.repo, "merge")
        self.assertEqual(len(lines), 1)
        self.assertIn("merge gate run 400s", lines[0])

    def test_merge_run_over_unit_limit_is_not_labelled_unit(self):
        G.record(self.repo, ["scripts/dev.sh", "test"], 0, secs=200.0, level="parallel", kind=GATE_KIND_MERGE)
        self.assertEqual(T.check(self.repo, "unit"), [])

    def test_run_without_kind_is_not_timed(self):
        G.record(self.repo, ["scripts/dev.sh", "test"], 0, secs=900.0, level="serial")
        self.assertEqual(T.check(self.repo, "unit"), [])
        self.assertEqual(T.check(self.repo, "merge"), [])

    def test_run_without_secs_is_skipped(self):
        G.record(self.repo, ["scripts/dev.sh", "test"], 0, kind=GATE_KIND_TASK)
        self.assertEqual(T.check(self.repo, "unit"), [])

    def test_no_repo_gives_nothing(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(T.check(plain, "unit"), [])


class TestLogGateCmds(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "metrics.jsonl")

    def test_level_and_warn_line_rc_unchanged(self):
        results = [("scripts/dev.sh test", 0, 200.0, ""), ("scripts/dev.sh check", 1, 5.0, "")]
        warns = final_check.log_gate_cmds(self.path, "task", results, {}, "task-3", (), LIMITS)
        rows = [json.loads(x) for x in open(self.path, encoding="utf-8").read().splitlines()]
        self.assertEqual([r["level"] for r in rows], ["unit", "unit"])
        self.assertEqual([r["rc"] for r in rows], [0, 1])
        self.assertEqual(len(warns), 1)
        self.assertIn("scripts/dev.sh test", warns[0])

    def test_no_limits_no_warn_lines(self):
        warns = final_check.log_gate_cmds(self.path, "merge", [("x", 0, 999.0, "")], {})
        self.assertEqual(warns, [])
        self.assertEqual(json.loads(open(self.path, encoding="utf-8").read())["level"], "merge")


if __name__ == "__main__":
    unittest.main()
