"""pipeline.json `registry_files`: a task whose text matches a rule must list the registry file in its Predicted paths, so the overlap rule gives such tasks separate waves."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.configuration import ConfigError, project
from tests.support.tmpdirs import tracked_mkdtemp

REG = "crates/core/codes.rs"
RULE = {"path": REG, "when": r"adds? an? validator code"}


class RegistryFilesTest(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def cfg(self, value):
        Path(self.root, "pipeline.json").write_text(json.dumps({"registry_files": value}), encoding="utf-8")

    def task(self, n, body, paths):
        Path(self.d, f"task-{n}.md").write_text(f"# T{n}\n{body}\nPredicted paths: {paths}\n", encoding="utf-8")
        return {"id": f"task-{n}", "wave": 1}

    def test_a_task_that_adds_a_code_without_listing_the_file_is_a_problem(self):
        self.cfg([RULE])
        ents = [self.task(5, "This task adds a validator code.", f"{REG}, src/a.rs"), self.task(7, "It adds a validator code X.", "src/b.rs")]
        got = plan_check.registry_problems(self.d, ents)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("task-7:"))
        self.assertIn(REG, got[0])
        self.assertEqual(plan_check.errors_of(got), got)  # fails the check

    def test_listed_or_unrelated_tasks_pass(self):
        self.cfg([RULE])
        ents = [self.task(1, "adds a validator code", REG), self.task(2, "renames a helper", "src/c.rs")]
        self.assertEqual(plan_check.registry_problems(self.d, ents), [])

    def test_no_rules_no_problems(self):
        self.assertEqual(plan_check.registry_problems(self.d, [self.task(1, "adds a validator code", "x")]), [])

    def test_two_tasks_listing_it_cannot_share_a_wave(self):
        self.cfg([RULE])
        ents = [self.task(5, "adds a validator code", REG), self.task(7, "adds a validator code", REG)]
        probs = plan_check._overlap_problems(self.d, ents)
        self.assertTrue(any("share paths in wave" in p for p in probs), probs)

    def test_bad_rules_are_config_errors(self):
        for bad in ("x", [{"path": REG}], [{"when": "a"}], [{"path": REG, "when": "("}], ["s"]):
            self.cfg(bad)
            with self.assertRaises(ConfigError, msg=str(bad)):
                project(self.root)


if __name__ == "__main__":
    unittest.main()
