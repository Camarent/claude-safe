"""plan_research.check_research: a research task has one tracked Outputs: path and a later task that depends on it names that path; code and report tasks are not checked."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_kinds, plan_research

RESEARCH = "task-1"  # the research task; asserts use these names, not the literals (path-dependent check)
CONSUMER = "task-2"  # a later task
OUT = "docs/findings.md"  # the one tracked output of the research task


class ResearchTest(unittest.TestCase):
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

    def ents(self, consumer_depends=(RESEARCH,)):
        return [
            {"id": RESEARCH, "depends": []},
            {"id": CONSUMER, "depends": list(consumer_depends)},
        ]

    def research(self, outputs=f"Outputs: {OUT}\n"):
        self.task(RESEARCH, f"Type: research\nPredicted paths: {OUT}\n{outputs}")

    def check(self, ents):
        return plan_research.check_research(self.d, ents[0], ents, self.root)

    def test_valid_research_with_consumer_passes(self):
        self.research()
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead {OUT} first.\n")
        self.assertEqual(self.check(self.ents()), [])

    def test_no_outputs_fails(self):
        self.task(RESEARCH, "Type: research\n")
        self.assertEqual(len(self.check(self.ents())), 1)
        self.assertIn(f"research: {RESEARCH}: a research task names one tracked output", self.check(self.ents())[0])

    def test_two_outputs_fail(self):
        self.research(outputs=f"Outputs: {OUT}, docs/other.md\n")
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead {OUT}.\n")
        probs = self.check(self.ents())
        self.assertEqual(len(probs), 1)
        self.assertIn("found 2", probs[0])

    def test_git_ignored_output_fails(self):
        self.research(outputs="Outputs: build/out.bin\n")
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead build/out.bin.\n")
        probs = self.check(self.ents())
        self.assertEqual(len(probs), 1)
        self.assertIn(f"outputs: {RESEARCH}: output `build/out.bin` is under the work folder or git-ignored", probs[0])

    def test_work_folder_output_fails(self):
        self.research(outputs="Outputs: .work/s/out.md\n")
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead .work/s/out.md.\n")
        probs = self.check(self.ents())
        self.assertTrue(any("is under the work folder" in p for p in probs))

    def test_no_consumer_fails(self):
        self.research()
        self.task(CONSUMER, "Type: code\n")
        probs = self.check(self.ents(consumer_depends=()))
        self.assertEqual(len(probs), 1)
        self.assertIn(f"no later task depends on {RESEARCH} and names its output `{OUT}`", probs[0])

    def test_consumer_naming_other_path_fails(self):
        self.research()
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead docs/other.md.\n")
        probs = self.check(self.ents())
        self.assertEqual(len(probs), 1)
        self.assertIn("no later task depends", probs[0])

    def test_path_named_without_depends_fails(self):
        self.research()
        self.task(CONSUMER, f"Type: code\nRead {OUT} first.\n")
        probs = self.check(self.ents(consumer_depends=()))
        self.assertEqual(len(probs), 1)
        self.assertIn("no later task depends", probs[0])

    def test_earlier_task_is_not_a_consumer(self):
        self.research()
        ents = [{"id": CONSUMER, "depends": []}, {"id": RESEARCH, "depends": []}]
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead {OUT}.\n")
        probs = plan_research.check_research(self.d, ents[1], ents, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("no later task depends", probs[0])

    def test_code_task_is_not_checked(self):
        self.task(RESEARCH, "Type: code\n")
        self.assertEqual(self.check(self.ents()), [])

    def test_report_task_is_not_checked(self):
        self.task(RESEARCH, "Type: report\n")
        self.assertEqual(self.check(self.ents()), [])

    def test_check_kinds_routes_research(self):
        self.research()
        self.task(CONSUMER, f"Type: code\nDepends: {RESEARCH}\nRead {OUT}.\n")
        self.assertEqual(plan_kinds.check_kinds(self.d, self.ents()[0], self.ents(), self.root), [])
        self.task(CONSUMER, "Type: code\n")
        self.assertEqual(len(plan_kinds.check_kinds(self.d, self.ents()[0], self.ents(), self.root)), 1)
