"""pipeline.affected: the test files that import the module of each planned path, on a fixture tree."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import affected


class ImportersTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "tools")
        self.tests = os.path.join(self.root, "tests")
        self.put("pipeline/plan_review.py", "X = 1\n")
        self.put("pipeline/plan_files.py", "Y = 2\n")
        self.put("tests/test_review.py", "from pipeline import plan_review\n")
        self.put("tests/test_direct.py", "import pipeline.plan_review as pr\n")
        self.put("tests/test_files.py", "from pipeline.plan_files import Y\n")
        self.put("tests/test_unrelated.py", "from pipeline import plan_check\nnote = 'pipeline.plan_review'\n")
        self.put("tests/sub/test_deep.py", "from pipeline.plan_review import X\n")

    def put(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def planned(self, rel):
        return os.path.join(self.root, rel)

    def test_finds_every_importer_of_the_planned_module(self):
        got = affected.importers([self.planned("pipeline/plan_review.py")], self.tests, self.root)
        self.assertEqual(got, ["sub/test_deep.py", "test_direct.py", "test_review.py"])

    def test_other_module_has_its_own_importers(self):
        got = affected.importers([self.planned("pipeline/plan_files.py")], self.tests, self.root)
        self.assertEqual(got, ["test_files.py"])

    def test_string_mention_and_other_module_are_not_importers(self):
        got = affected.importers([self.planned("pipeline/plan_review.py")], self.tests, self.root)
        self.assertNotIn("test_unrelated.py", got)

    def test_path_outside_root_gives_nothing(self):
        self.assertEqual(affected.importers(["elsewhere/plan_review.py"], self.tests, self.root), [])

    def test_no_planned_paths_gives_nothing(self):
        self.assertEqual(affected.importers([], self.tests, self.root), [])

    def test_module_name_of_package_init(self):
        self.assertEqual(affected.module_name(self.planned("pipeline/__init__.py"), self.root), "pipeline")
        self.assertEqual(affected.module_name(self.planned("pipeline/plan_review.py"), self.root), "pipeline.plan_review")


class AgentInstructionTest(unittest.TestCase):
    def read_agent(self, name):
        agents = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agents")
        with open(os.path.join(agents, name), encoding="utf-8") as fh:
            return fh.read()

    def test_architect_and_reviewer_name_the_affected_cli(self):
        for name in ("architect.md", "plan-reviewer.md"):
            self.assertIn("affected.py", self.read_agent(name), name)
