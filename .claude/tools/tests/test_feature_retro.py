"""retro/feature_retro.py: the problems file and the status line the driver turns into the step's verdict."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gate_record
from pipeline.configuration import EV_GATE_CMD, GATE_KIND_TASK, METRICS_LOG, WORK_DIR
from retro import feature_retro
from test_gate_record import make_repo


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

    def test_block_causes_line_comes_from_the_script_counts(self):  # no model asked: the digest carries the counts of the report events
        import json

        with open(os.path.join(self.dir, "phases", "01-a", "orch.jsonl"), "w") as fh:
            events = [
                {"ev": "report", "task": "task-1", "verdict": "BLOCKED", "reason": "cite check: path missing"},
                {"ev": "report", "task": "task-2", "verdict": "FAIL", "reason": "gate red after 2 tries"},
                {"ev": "report", "task": "task-3", "verdict": "DONE", "reason": "ok"},
            ]
            fh.writelines(json.dumps(e) + "\n" for e in events)
        text = feature_retro.render("f", feature_retro.gather(self.dir))
        self.assertIn("## Block causes (script, no model)\n- block causes (2 BLOCKED/FAIL reports): cite 1, gate 1, ignored path 0, tooling 0, other 0", text)

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

    def test_gate_time_section_lists_an_over_limit_unit_run(self):
        repo = make_repo(os.path.join(self.dir, "ws"), {"a.txt": "x\n"})
        gate_record.record(repo, ["scripts/dev.sh", "test"], 0, secs=200.0, level="parallel", kind=GATE_KIND_TASK)
        text = feature_retro.render("f", feature_retro.gather(self.dir), feature_retro.gate_lines(self.dir, repo))
        self.assertIn("## Gate time and dev gate warnings", text)
        self.assertIn("- gate time: unit gate run 200s over the", text)
        self.assertNotIn("## Gate time and dev gate warnings\n(none)", text)

    def test_gate_time_section_is_none_within_the_limit(self):
        repo = make_repo(os.path.join(self.dir, "ws"), {"a.txt": "x\n"})
        gate_record.record(repo, ["scripts/dev.sh", "test"], 0, secs=10.0, level="serial", kind=GATE_KIND_TASK)
        text = feature_retro.render("f", feature_retro.gather(self.dir), feature_retro.gate_lines(self.dir, repo))
        self.assertIn("## Gate time and dev gate warnings\n(none)", text)

    def _repo(self):
        """The repo of the feature (its work folder exists, as the stage folders of a real run sit under it)."""
        path = os.path.join(self.dir, "ws")
        if not os.path.isdir(path):
            repo = make_repo(path, {"a.txt": "x\n"})
            os.makedirs(os.path.join(repo, WORK_DIR))
        return path

    def _gate_cmd_rows(self, *rows):
        """Write gate_cmd metrics rows into the phase 01-a of the feature (the metrics.jsonl the retro reads)."""
        self._repo()
        with open(os.path.join(self.dir, "phases", "01-a", METRICS_LOG), "w") as fh:
            fh.writelines(json.dumps({"ev": EV_GATE_CMD, **r}) + "\n" for r in rows)

    def _gate_stat_lines(self):
        return [x for x in feature_retro.gate_lines(self.dir, self._repo()) if x.startswith("gate stats ")]

    def test_gate_stats_count_old_records_without_mode_concurrent_or_skipped_as_real_runs(self):
        self._gate_cmd_rows(
            {"kind": "task", "level": "unit", "seconds": 10.0},
            {"kind": "task", "level": "unit", "seconds": 30.0},
            {"kind": "task", "level": "unit", "seconds": 20.0},
        )
        lines = self._gate_stat_lines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^gate stats unit: 3 runs, 0 skipped, median 20\.0s, p90 30\.0s, limit \d+s$")

    def test_gate_stats_do_not_count_skipped_runs_as_real_runs(self):
        self._gate_cmd_rows(
            {"kind": "task", "level": "unit", "seconds": 12.0, "skipped": False},
            {"kind": "task", "level": "unit", "seconds": 0.0, "skipped": True},
            {"kind": "task", "level": "unit", "seconds": 0.0, "skipped": True},
        )
        lines = self._gate_stat_lines()
        self.assertRegex(lines[0], r"^gate stats unit: 1 runs, 2 skipped, median 12\.0s, p90 12\.0s, limit \d+s$")

    def test_gate_stats_of_an_all_skipped_group_print_dashes(self):
        self._gate_cmd_rows(
            {"kind": "merge", "level": "merge", "seconds": 0.0, "skipped": True},
            {"kind": "merge", "level": "merge", "seconds": 0.0, "skipped": True},
        )
        lines = self._gate_stat_lines()
        self.assertRegex(lines[0], r"^gate stats merge: 0 runs, 2 skipped, median -, p90 -, limit \d+s$")

    def test_gate_stats_take_the_level_of_the_kind_when_a_record_has_none(self):
        self._gate_cmd_rows({"kind": "full", "seconds": 5.0})
        lines = self._gate_stat_lines()
        self.assertRegex(lines[0], r"^gate stats full: 1 runs, 0 skipped, median 5\.0s, p90 5\.0s, limit \d+s$")

    def test_gate_stats_print_nothing_without_metrics(self):
        self.assertEqual(self._gate_stat_lines(), [])

    def test_gate_stats_skip_when_the_pipeline_config_is_invalid(self):
        self._gate_cmd_rows({"kind": "task", "level": "unit", "seconds": 5.0})
        with open(os.path.join(self._repo(), "pipeline.json"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(self._gate_stat_lines(), [])


if __name__ == "__main__":
    unittest.main()
