"""plan_research.check_research: a research task whose Goal needs web access fails (scout has no web); a repo-only research Goal passes."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_research

RESEARCH = "task-1"  # the research task; asserts use these names, not the literals (path-dependent check)
CONSUMER = "task-2"  # a later task
OUT = "docs/findings.md"  # the one tracked output of the research task


class ResearchWebTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        with open(os.path.join(self.root, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("build/\n")
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def task(self, name, body):
        with open(os.path.join(self.d, name + ".md"), "w", encoding="utf-8") as f:
            f.write(body)

    def ents(self):
        return [{"id": RESEARCH, "depends": []}, {"id": CONSUMER, "depends": [RESEARCH]}]

    def research(self, goal):
        self.task(RESEARCH, f"Type: research\nGoal: {goal}\nPredicted paths: {OUT}\nOutputs: {OUT}\n")
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead {OUT} first.\n")

    def check(self):
        ents = self.ents()
        return plan_research.check_research(self.d, ents[0], ents, self.root)

    def test_web_goal_fails(self):
        self.research("read the upstream docs at https://example.org/api for the retry rule")
        probs = self.check()
        self.assertEqual(len(probs), 1)
        self.assertIn(f"research-web: {RESEARCH}: its Goal needs web access", probs[0])
        self.assertIn("orch.py report --replace", probs[0])
        self.assertIn(".claude/agents/researcher.md", probs[0])

    def test_online_goal_fails(self):
        self.research("find the limits published online")
        probs = self.check()
        self.assertEqual(len(probs), 1)
        self.assertIn(f"research-web: {RESEARCH}", probs[0])

    def test_repo_goal_with_web_word_passes(self):
        self.research("find the web routes in this repo")
        self.assertEqual(self.check(), [])

    def test_repo_only_goal_passes(self):
        self.research("find which module builds the plan check list in this repo")
        self.assertEqual(self.check(), [])
