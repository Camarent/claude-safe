"""Check results must not depend on the worktree path: a job worktree folder (`task-1/claude-safe/x`) is no task mention."""

import argparse
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.configuration.planning import PREREQ_WARN_PREFIX
from pipeline.plan_files import PHASE_HEAD

TASKS = (
    "## Tasks\n"
    "- [ ] task-1 one - owner: haiku - review: wave W2, haiku\n"
    "- [ ] task-2 two - owner: haiku - review: wave W1, haiku\n"
    "\n## Graph\nwave 1: task-2 | wave 2: task-1\n"
)


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class WorktreePathTest(unittest.TestCase):
    def base(self):
        base = tempfile.mkdtemp(prefix="synthetic-worktree-")
        self.addCleanup(shutil.rmtree, base, True)
        return base

    def stage(self, root, rel):
        """Synthetic stage at ROOT/REL: task-1 (wave 2) names task-2 in prose; task-2 (wave 1) names ROOT in its workdir line."""
        d = os.path.join(root, rel)
        put(os.path.join(d, "TASKS.md"), TASKS)
        put(os.path.join(d, "task-1.md"), "# T1\nPredicted paths: src/a.py\nNeeds the parser output of task-2 first.\n")
        put(os.path.join(d, "task-2.md"), f"# T2\nPredicted paths: src/b.py\nWorkdir: {root}\n")
        return d

    def stage_problems(self, root):
        d = self.stage(root, os.path.join(".work", "s"))
        n, probs = plan_check.check_stage(d, repo=root)
        return n, [p.replace(root, "ROOT") for p in probs]

    def feature_problems(self, root):
        fdir = os.path.join(root, ".work", "f")
        plan = f"# Plan: f\n\n## Goal\ng\n\n## Acceptance checklist\n- cmd: true\n\n## Phases\n{PHASE_HEAD}"
        plan += "| 01-a | g | true |  | src/a | todo | 20 |\n"
        put(os.path.join(fdir, "Plan.md"), plan)
        self.stage(fdir, os.path.join("phases", "01-a"))
        n, probs = plan_check.check_feature(argparse.Namespace(dir=root, feature="f"), fdir)
        return n, [p.replace(root, "ROOT") for p in probs]

    def test_stage_result_same_under_worktree_path(self):
        in_worktree = self.stage_problems(os.path.join(self.base(), "task-1", "claude-safe", "x"))
        plain = self.stage_problems(os.path.join(self.base(), "plain"))
        self.assertEqual(in_worktree, plain)
        self.assertEqual(plain[0], 2)
        self.assertEqual([p for p in plain[1] if p.startswith(PREREQ_WARN_PREFIX)], [])

    def test_feature_result_same_under_worktree_path(self):
        in_worktree = self.feature_problems(os.path.join(self.base(), "task-1", "claude-safe", "x"))
        plain = self.feature_problems(os.path.join(self.base(), "plain"))
        self.assertEqual(in_worktree, plain)
        self.assertEqual(plain[0], 2)  # the phase's two tasks
        self.assertEqual([p for p in plain[1] if p.startswith(PREREQ_WARN_PREFIX)], [])
