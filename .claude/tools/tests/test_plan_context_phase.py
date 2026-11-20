"""plan_context phase: the context file for the architect of phase 2..N. Throwaway git repo, model stubbed, no lp/Strata/Claude."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import plan_context as pc
from pipeline import cli_feature, cli_plan, cli_wave
from pipeline import phase_context as ph
from pipeline import plan_files as pf
from tests.support.tmpdirs import tracked_mkdtemp

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "safe.directory=*"]


_NO_EXPLORE = mock.patch("localmodel.explore.explore", return_value=None)  # the explorer would call a real Strata


def setUpModule():
    _NO_EXPLORE.start()


def tearDownModule():
    _NO_EXPLORE.stop()


def git(d, *a):
    subprocess.run([*GIT, *a], cwd=d, check=True, capture_output=True)


def stubs():
    return mock.patch.object(pc, "model_files", return_value=[]), mock.patch.object(ph, "ensure_index", return_value=[])


class PhaseContext(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")
        Path(self.d, "seed.txt").write_text("x")
        git(self.d, "add", ".")
        git(self.d, "commit", "-qm", "seed")
        git(self.d, "checkout", "-qb", "stage/F")
        Path(self.d, "src").mkdir()
        Path(self.d, "src", "a.py").write_text('"""Phase a module."""\n')
        git(self.d, "add", ".")
        git(self.d, "commit", "-qm", "phase a")
        cli_plan.main(["plan-init", "--feature", "F", "--goal", "g", "--dir", self.d])
        for n in ("a", "b"):
            cli_plan.main(["phase", "--feature", "F", "--name", n, "--goal", f"goal of {n}", "--done-when", "true", "--paths", f"src/{n}", "--dir", self.d])
        self.fdir = os.path.join(self.d, ".work", "F")
        a_dir = os.path.join(self.fdir, "phases", "01-a")
        os.makedirs(a_dir, exist_ok=True)
        Path(a_dir, "TASKS.md").write_text("- [ ] task-1 Build the a module - owner: dev - review: wave W1, haiku\n")
        self.rows = pf.read_phases(cli_wave.argparse.Namespace(dir=self.d, feature="F"))

    def build(self, named=None, notes=None):
        if notes is not None:
            Path(self.fdir, "plan-notes.md").write_text(notes)
        with mock.patch.object(pc, "model_files", return_value=named or []), mock.patch.object(ph, "ensure_index", return_value=[]):
            return pc.phase_context(self.d, self.fdir, self.rows[1], pc.CTX_DEFAULTS)

    def test_explore_mode_names_files(self):
        with (
            mock.patch.object(ph, "ensure_index", return_value=[]),
            mock.patch("localmodel.explore.explore", return_value=[(os.path.basename(self.d), "src/a.py", "owns the glue", "code")]),
        ):
            t = pc.phase_context(self.d, self.fdir, self.rows[1], pc.CTX_DEFAULTS)
        self.assertIn("src/a.py", t)
        self.assertIn("owns the glue", t)

    def test_pieces_present(self):
        t = self.build(["src/a.py"], "D1: use src/b for the glue\n")
        for s in ("goal of b", "src/b", "done when: true", "D1: use src/b", "src/a.py", "task-1: Build the a module", "01-a"):
            self.assertIn(s, t)

    def test_strata_down_note(self):
        self.assertIn("(local model down", self.build())

    def test_big_notes_filtered_and_capped(self):
        notes = "\n".join([f"noise line {i}" for i in range(2000)] + ["D9 about src/b only"])
        t = self.build(notes=notes)
        self.assertIn("D9 about src/b only", t)
        self.assertNotIn("noise line 5", t)
        self.assertLess(len(t.partition("## decisions")[2].partition("## merged")[0]), ph.NOTES_CAP + 100)

    def test_big_notes_keep_the_decisions_section_even_when_no_line_names_the_phase(self):
        notes = "\n".join(["## Evidence checked"] + [f"evidence line {i}" for i in range(1500)] + ["## Decisions", "D1 use a separate path", "D2 keep ids"])
        t = self.build(notes=notes)
        self.assertIn("D1 use a separate path", t)
        self.assertIn("D2 keep ids", t)
        self.assertNotIn("evidence line 5\n", t)

    def test_big_notes_with_no_match_and_no_decisions_heading_fall_back_to_the_head(self):
        t = self.build(notes="\n".join(f"plain line {i}" for i in range(2000)))
        self.assertIn("plain line 0", t)
        self.assertNotIn("(none)", t.partition("## decisions")[2].partition("## merged")[0])

    def test_earlier_phase_titles_are_not_cut_at_the_diff_stat_cap(self):
        many = "".join(f"- [ ] task-{i} Title number {i} - owner: dev - review: wave W1, haiku\n" for i in range(1, 61))
        Path(self.fdir, "phases", "01-a", "TASKS.md").write_text(many)
        t = self.build()
        self.assertIn("task-60: Title number 60", t)
        self.assertGreater(60, ph.STAT_CAP)

    def test_big_notes_up_to_the_cap_are_kept_whole(self):
        notes = "## Decisions\n" + "\n".join(f"D{i} decision text {i}" for i in range(1, 300))
        self.assertLess(len(notes), ph.NOTES_CAP)
        self.assertIn("D299 decision text 299", self.build(notes=notes))

    def test_diff_stat_of_merged_work(self):
        self.assertIn("src/a.py", self.build().partition("## merged")[2])

    def test_cli_writes_default_file(self):
        m, e = stubs()
        with m, e, redirect_stdout(io.StringIO()):
            self.assertEqual(pc.main(["phase", self.d, "--feature", "F", "--phase", "02-b", "--workspace", self.d]), 0)
            self.assertEqual(pc.main(["phase", self.d, "--feature", "F", "--phase", "nope", "--workspace", self.d]), 2)
        self.assertTrue(os.path.exists(os.path.join(self.fdir, "phases", "02-b", "plan-context.md")))

    def test_prompt_mentions_file_only_when_it_exists(self):
        a = cli_wave.argparse.Namespace(feature="F", dir=self.d)
        self.assertNotIn("plan-context.md", cli_feature.plan_prompt(a, self.rows[1]))
        m, e = stubs()
        with m, e, mock.patch.dict(os.environ), mock.patch.object(os.environ, "pop", wraps=os.environ.pop):
            os.environ.pop("PLAN_NO_CONTEXT", None)
            cli_feature.phase_ctx(a, self.rows[1])
        self.assertIn("hints to verify", cli_feature.plan_prompt(a, self.rows[1]))
        with open(os.path.join(self.fdir, "metrics.jsonl"), encoding="utf-8") as f:
            ev = [json.loads(x) for x in f][-1]
        self.assertEqual((ev["ev"], ev["phase"]), ("context", "02-b"))

    def test_dry_run_builds_nothing(self):
        a = cli_wave.argparse.Namespace(feature="F", dir=self.d, dry_run=True, max_waves=None, max_dispatches=None, max_sessions=None, max_budget_usd=None)
        with mock.patch.object(pc, "phase_context") as m, mock.patch.object(ph, "ensure_index") as e, redirect_stdout(io.StringIO()):
            cli_feature.cmd_feature(a)
        m.assert_not_called()
        e.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.fdir, "phases", "02-b", "plan-context.md")))


if __name__ == "__main__":
    unittest.main()
