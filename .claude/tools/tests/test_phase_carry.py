"""Later phases see what earlier phases left: the planner prompt, the carried-items section of TASKS.md (plan_check), the earlier-phase digest."""

import argparse
import io
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_feature, cli_plan, plan_check
from pipeline import phase_context as ph
from pipeline import plan_files as pf
from pipeline.configuration import CARRIED_SECTION, DIGEST_LINES_CAP
from tests.support.tmpdirs import tracked_mkdtemp

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "safe.directory=*"]
TASK_LINE = "- [ ] task-1 Build the a module - owner: dev - review: wave W1, haiku\n"


def git(d, *a):
    subprocess.run([*GIT, *a], cwd=d, check=True, capture_output=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")
        Path(self.d, "seed.txt").write_text("x")
        git(self.d, "add", ".")
        git(self.d, "commit", "-qm", "seed")
        git(self.d, "checkout", "-qb", "stage/F")
        cli_plan.main(["plan-init", "--feature", "F", "--goal", "g", "--dir", self.d])
        for n in ("a", "b", "finish"):
            cli_plan.main(["phase", "--feature", "F", "--name", n, "--goal", f"goal of {n}", "--done-when", "true", "--paths", f"src/{n}", "--dir", self.d])
        self.fdir = os.path.join(self.d, ".work", "F")
        self.a_dir = os.path.join(self.fdir, "phases", "01-a")
        os.makedirs(os.path.join(self.a_dir, "reports"))
        os.makedirs(os.path.join(self.a_dir, "audits"))
        Path(self.a_dir, "TASKS.md").write_text(TASK_LINE)
        self.rows = pf.read_phases(argparse.Namespace(dir=self.d, feature="F"))
        self.addCleanup(lambda: shutil.rmtree(self.d, ignore_errors=True))

    def phase(self, dir_name):
        return next(r for r in self.rows if r["dir"] == dir_name)


class PlannerPrompt(_Base):
    def test_later_phase_prompt_reads_earlier_reports_audits_and_tasks(self):
        prompt = cli_feature.plan_prompt(argparse.Namespace(dir=self.d, feature="F"), self.phase("02-b"))
        self.assertIn("phases/01-a/reports", prompt)
        self.assertIn("FAIL", prompt)
        self.assertIn("DEVIATION", prompt)
        self.assertIn("DOCS-STALE", prompt)
        self.assertIn("phases/01-a/audits", prompt)
        self.assertIn(f"## {CARRIED_SECTION}", prompt)
        self.assertIn("not needed: reason", prompt)

    def test_first_phase_prompt_has_no_carried_rule(self):
        prompt = cli_feature.plan_prompt(argparse.Namespace(dir=self.d, feature="F"), self.phase("01-a"))
        self.assertNotIn(CARRIED_SECTION, prompt)


class CarriedSection(_Base):
    def _check(self, dir_name, text):
        folder = os.path.join(self.fdir, "phases", dir_name)
        os.makedirs(folder, exist_ok=True)
        Path(folder, "TASKS.md").write_text(text)
        return plan_check.check_stage(folder, self.d)[1]

    def test_later_phase_without_section_is_rejected(self):
        probs = self._check("02-b", TASK_LINE)
        self.assertTrue(any(CARRIED_SECTION in p for p in probs), probs)

    def test_later_phase_with_task_and_not_needed_lines_passes(self):
        text = TASK_LINE + f"\n## {CARRIED_SECTION}\n- task-1 covers the a module gap\n- not needed: a is only read here\n"
        probs = self._check("02-b", text)
        self.assertFalse(any(CARRIED_SECTION in p for p in probs), probs)

    def test_carried_line_that_is_neither_task_nor_not_needed_is_rejected(self):
        text = TASK_LINE + f"\n## {CARRIED_SECTION}\n- task-1 covers it\n- maybe later\n"
        probs = self._check("02-b", text)
        self.assertTrue(any("maybe later" in p for p in probs), probs)

    def test_adding_a_task_keeps_the_carried_section(self):
        folder = os.path.join(self.fdir, "phases", "02-b")
        text = (
            "# Stage: s\nGoal: g\n\n## Tasks\n- task-1 A - review: wave W1\n\n## Graph\nwave 1: task-1\n\n"
            f"## {CARRIED_SECTION}\n- task-1 covers the old finding\n"
        )
        os.makedirs(folder, exist_ok=True)
        Path(folder, "TASKS.md").write_text(text)
        entries = pf.read_entries(folder)
        pf.write_tasks(folder, [*entries, {"id": "task-2", "title": "B", "owner": "", "wave": 2, "review": "", "depends": ["task-1"]}])
        after = Path(folder, "TASKS.md").read_text()
        self.assertIn("- task-2 B", after)
        self.assertIn(f"## {CARRIED_SECTION}\n- task-1 covers the old finding", after)
        self.assertEqual(after.count("## Graph"), 1)
        pf.write_tasks(folder, pf.read_entries(folder))  # a second write changes nothing
        self.assertEqual(Path(folder, "TASKS.md").read_text(), after)

    def test_first_phase_is_not_checked(self):
        probs = self._check("01-a", TASK_LINE)
        self.assertFalse(any(CARRIED_SECTION in p for p in probs), probs)


class Digest(_Base):
    def _write(self, rel, text):
        p = os.path.join(self.a_dir, rel)
        Path(p).write_text(text)

    def test_digest_names_fail_deviation_docs_stale_and_open_fixes(self):
        self._write("reports/report-a1.md", "VERDICT: FAIL\nDOCS-STALE: docs/x.md - old name\n")
        self._write("audits/audit-sec.md", "VERDICT: FIXES\nDEVIATION: touched src/zzz\n")
        items = ph.earlier_open_items(self.fdir, self.phase("02-b"), self.d)
        joined = "\n".join(items)
        self.assertIn("FAIL", joined)
        self.assertIn("DOCS-STALE: docs/x.md", joined)
        self.assertIn("FIXES", joined)
        self.assertIn("DEVIATION: touched src/zzz", joined)

    def test_clean_earlier_phase_has_no_open_items(self):
        self._write("audits/audit-sec.md", "VERDICT: CLEAN\n")
        self.assertEqual(ph.earlier_open_items(self.fdir, self.phase("02-b"), self.d), [])

    def test_digest_is_capped(self):
        for i in range(DIGEST_LINES_CAP + 20):
            self._write(f"reports/report-x{i}.md", f"VERDICT: FAIL\nDOCS-STALE: docs/{i}.md - stale\n")
        items = ph.earlier_open_items(self.fdir, self.phase("02-b"), self.d)
        text = ph.phase_digest(self.fdir, self.phase("02-b"), self.d)
        self.assertGreater(len(items), DIGEST_LINES_CAP)
        self.assertLessEqual(sum("FAIL" in x for x in text), DIGEST_LINES_CAP)

    def test_digest_failure_is_logged_not_silent(self):
        err = io.StringIO()
        with mock.patch.object(ph, "_task_lines", side_effect=OSError("boom")), redirect_stderr(err):
            lines = ph.phase_digest(self.fdir, self.phase("02-b"), self.d)
        self.assertIn("boom", err.getvalue())
        self.assertTrue(any("digest part failed" in x for x in lines), lines)

    def test_digest_has_stat_for_every_repo(self):
        with mock.patch.object(ph, "repo_dirs", return_value=[self.d, self.d]):
            text = "\n".join(ph.phase_digest(self.fdir, self.phase("02-b"), self.d))
        self.assertEqual(text.count("### "), 2, text)


if __name__ == "__main__":
    unittest.main()
