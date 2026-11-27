"""The later-notes file (workspace root) joins the Carried check: an item naming a path of a stage task needs a `task-N` or `not needed:` line in `## Carried from earlier phases`."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_check
from pipeline.configuration import CARRIED_SECTION, LATER_NOTES_PATH
from tests.support.tmpdirs import tracked_mkdtemp

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "safe.directory=*"]
ROUTER = "extension/src/ui/router.ts"
TASK_ROW = "- [ ] task-1 Route the ui - owner: dev - review: wave W1, haiku\n"
CARRIED_TASK = f"\n## {CARRIED_SECTION}\n- task-1 covers the router gap\n"
CARRIED_EMPTY = f"\n## {CARRIED_SECTION}\n"


def git(d, *a):
    subprocess.run([*GIT, *a], cwd=d, check=True, capture_output=True)


class LaterNotes(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.d, ignore_errors=True))
        git(self.d, "init", "-q", "-b", "master")
        Path(self.d, "seed.txt").write_text("x")
        git(self.d, "add", ".")
        git(self.d, "commit", "-qm", "seed")
        git(self.d, "checkout", "-qb", "stage/F")
        cli_plan.main(["plan-init", "--feature", "F", "--goal", "g", "--dir", self.d])
        for n in ("a", "b"):
            cli_plan.main(["phase", "--feature", "F", "--name", n, "--goal", f"goal of {n}", "--done-when", "true", "--paths", f"src/{n}", "--dir", self.d])
        self.folder = os.path.join(self.d, ".work", "F", "phases", "02-b")
        os.makedirs(self.folder, exist_ok=True)
        Path(self.folder, "task-1.md").write_text(f"# task-1\nPredicted paths: {ROUTER}\n")

    def _notes(self, text):
        path = os.path.join(self.d, LATER_NOTES_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text(text)

    def _check(self, carried):
        Path(self.folder, "TASKS.md").write_text(TASK_ROW + carried)
        return plan_check.check_stage(self.folder, self.d)[1]

    def _notes_probs(self, probs):
        return [p for p in probs if p.startswith("carried-notes:")]

    def test_path_item_without_carried_bullet_is_rejected(self):
        self._notes(f"- {ROUTER} still needs a look\n")
        probs = self._check(CARRIED_EMPTY)
        self.assertTrue(self._notes_probs(probs), probs)

    def test_path_item_with_task_bullet_passes(self):
        self._notes(f"- {ROUTER} still needs a look\n")
        probs = self._check(CARRIED_TASK)
        self.assertFalse(self._notes_probs(probs), probs)

    def test_path_item_with_not_needed_bullet_passes(self):
        self._notes(f"- {ROUTER} still needs a look\n")
        probs = self._check(f"\n## {CARRIED_SECTION}\n- not needed: router is out of scope here\n")
        self.assertFalse(self._notes_probs(probs), probs)

    def test_item_for_path_no_task_lists_passes(self):
        self._notes("- docs/unrelated.md is stale\n")
        probs = self._check(CARRIED_EMPTY)
        self.assertFalse(self._notes_probs(probs), probs)

    def test_absent_notes_file_gives_none(self):
        probs = self._check(CARRIED_EMPTY)
        self.assertFalse(self._notes_probs(probs), probs)

    def test_first_phase_is_not_checked(self):
        first = os.path.join(self.d, ".work", "F", "phases", "01-a")
        os.makedirs(first, exist_ok=True)
        Path(first, "task-1.md").write_text(f"# task-1\nPredicted paths: {ROUTER}\n")
        Path(first, "TASKS.md").write_text(TASK_ROW)
        self._notes(f"- {ROUTER} still needs a look\n")
        probs = plan_check.check_stage(first, self.d)[1]
        self.assertFalse(self._notes_probs(probs), probs)
