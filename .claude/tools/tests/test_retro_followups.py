"""Retro follow-ups: stale findings in scans, ops sessions and late snapshots in the lead window, the real reason of a report,
the explore turn cap, the plan-context cache (goal + HEAD), predicted paths in the pack, the tests branch of context_paths."""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import scans
from localmodel import agent_files as af
from localmodel import plan_context as pc
from pipeline import configuration as cfg
from pipeline import phase_context as _phase
from pipeline import stage_recall as sr
from pipeline.cli_orch import _report_why
from retro import lead_cost
from retro import stale_findings as sf
from tests.support.tmpdirs import tracked_mkdtemp


class ReportWhy(unittest.TestCase):
    def test_handoff_line_is_not_the_reason(self):
        self.assertEqual(_report_why("VERDICT: BLOCKED\nno network for the test\nREPORT a2"), "no network for the test")

    def test_bare_verdict_gives_the_verdict_word(self):
        self.assertEqual(_report_why("VERDICT: BLOCKED\n\nREPORT a2\n"), "BLOCKED")

    def test_reason_on_verdict_line_wins(self):
        self.assertEqual(_report_why("VERDICT: FAIL: pytest x failed\nREPORT a2"), "pytest x failed")


class StaleScans(unittest.TestCase):
    def test_scans_section_lists_stale_cites(self):
        with mock.patch.object(sf, "stale_findings", return_value=[("a.py", 3, "maybe fixed"), ("b.py", 9, "open")]):
            text = scans.stale_section("report.md", "repo")
        self.assertIn("maybe fixed: a.py:3", text)
        self.assertIn("open: b.py:9", text)

    def test_no_report_no_section(self):
        self.assertEqual(scans.stale_section("", "repo"), "")


class LeadWindow(unittest.TestCase):
    def test_window_counts_metrics_snapshot_after_last_orch_event(self):
        d = tracked_mkdtemp()
        orch, metrics = os.path.join(d, "orch.jsonl"), os.path.join(d, "metrics.jsonl")
        with open(orch, "w") as f:
            f.write(json.dumps({"t": 100.0, "ev": "report"}) + "\n")
        with open(metrics, "w") as f:
            f.write(json.dumps({"ts": 200.0, "ev": "session"}) + "\n")
        self.assertEqual(lead_cost.window([orch, metrics]), (100.0, 200.0))

    def test_ops_session_is_a_lead(self):
        self.assertTrue(lead_cost._runs_pipeline_tool("python3 -I /x/ops_steps.py digest --dir D --stage S"))


class ExploreCap(unittest.TestCase):
    def test_explore_turn_cap_is_a_configuration_constant_of_eight(self):
        self.assertEqual(cfg.EXPLORE_MAX_TURNS, 8)
        self.assertEqual(af.MAX_TURNS, cfg.EXPLORE_MAX_TURNS)


class ContextPathsTests(unittest.TestCase):
    def test_dir_form_and_path_form_both_parse(self):
        text = "## files\n### tests\n- tests/unit/: a.py, b.py\n- tests/test_c.py: pins the cache\n### code\n- x.py - top\n"
        self.assertEqual(sr.context_paths(text), ["tests/unit/a.py", "tests/unit/b.py", "tests/test_c.py", "x.py"])


class PredictedMissing(unittest.TestCase):
    def test_predicted_file_absent_from_digest_is_listed(self):
        repo = tracked_mkdtemp()
        for name in ("a.py", "b.py"):
            open(os.path.join(repo, name), "w").close()
        self.assertEqual(_phase.predicted_missing(["a.py", "b.py", "gone.py"], ["- a.py - top"], repo), ["b.py"])


class PlanContextCache(unittest.TestCase):
    def setUp(self):
        self.repo = tracked_mkdtemp()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ["PATH"]}
        self.env = env
        self.git("init", "-q", "-b", "master")
        self.commit("one")
        self.feature = os.path.join(self.repo, ".work", "f")
        self.phase = {"dir": "01-x", "goal": "g", "paths": ["a.py"], "done_when": "d"}
        self.calls = 0

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo, *args], check=True, env=self.env, capture_output=True)

    def commit(self, msg):
        self.git("commit", "-q", "--allow-empty", "-m", msg)

    def build(self, *a):
        self.calls += 1
        return f"context {self.calls}\n"

    def ctx(self, phase=None):
        with mock.patch.object(_phase, "phase_context", side_effect=self.build):
            return pc.phase_context(self.repo, self.feature, phase or self.phase, None)

    def test_same_goal_same_head_reuses_the_text(self):
        self.assertEqual(self.ctx(), "context 1\n")
        self.assertEqual(self.ctx(), "context 1\n")  # a retry or an escalation: no rebuild
        self.assertEqual(self.calls, 1)

    def test_new_head_invalidates(self):
        self.ctx()
        self.commit("two")
        self.assertEqual(self.ctx(), "context 2\n")

    def test_degraded_build_is_not_cached(self):
        with mock.patch.object(_phase, "phase_context", side_effect=lambda *a: "# pack\n(local model down: timed out)\n"):
            pc.phase_context(self.repo, self.feature, self.phase, None)
        self.assertFalse(os.path.exists(os.path.join(self.feature, "phases", "01-x", pc.PHASE_CACHE)))
        self.assertEqual(self.ctx(), "context 1\n")  # the model is back: rebuilt
        self.assertEqual(self.ctx(), "context 1\n")  # and now cached
        self.assertEqual(self.calls, 1)

    def test_changed_goal_invalidates(self):
        self.ctx()
        self.assertEqual(self.ctx(dict(self.phase, goal="other")), "context 2\n")


if __name__ == "__main__":
    unittest.main()
