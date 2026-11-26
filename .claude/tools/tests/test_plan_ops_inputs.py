"""`Inputs:` of a task may name a missing file that an ops-N manual row provides, when the task depends on that row."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_report
from tests.support.tmpdirs import tracked_mkdtemp

T1, T2, OPS1 = "task-1", "task-2", "ops-1"
MISSING = "out/data.csv"


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class OpsInputsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="ops-inputs-")
        self.ws = tracked_mkdtemp(prefix="ops-ws-")

    def task(self, depends=()):
        _write(os.path.join(self.d, T1 + ".md"), f"# t\nInputs: {MISSING}\n")
        return [{"id": T1, "wave": 1, "depends": list(depends)}]

    def ops(self, text):
        return {"id": OPS1, "manual": text}

    def check(self, ents):
        return plan_report.check_type_inputs(self.d, T1, self.ws, self.ws, ents)

    def test_missing_path_without_ops_row_fails(self):
        got = self.check(self.task())
        self.assertEqual(len(got), 1)
        self.assertIn(MISSING, got[0])
        self.assertIn("ops-N", got[0])

    def test_ops_row_named_but_not_depended_on_fails(self):
        ents = [*self.task(), self.ops(f"copy the file to {MISSING}")]
        got = self.check(ents)
        self.assertEqual(len(got), 1)
        self.assertIn(OPS1, got[0])
        self.assertIn("Depends", got[0])

    def test_ops_row_named_and_depended_on_passes(self):
        ents = [*self.task(depends=[OPS1]), self.ops(f"copy the file to {MISSING}")]
        self.assertEqual(self.check(ents), [])

    def test_ops_row_naming_another_path_does_not_excuse(self):
        ents = [*self.task(depends=[OPS1]), self.ops("copy the file to out/other.csv")]
        got = self.check(ents)
        self.assertEqual(len(got), 1)
        self.assertIn(MISSING, got[0])

    def test_path_listed_by_a_stage_task_passes(self):
        _write(os.path.join(self.d, T2 + ".md"), f"# t2\nPredicted paths: {MISSING}\n")
        ents = [*self.task(), {"id": T2, "wave": 1, "depends": []}]
        self.assertEqual(self.check(ents), [])

    def test_without_stage_entries_the_old_error_stays(self):
        self.task()
        got = self.check(None)
        self.assertEqual(len(got), 1)
        self.assertIn("does not exist", got[0])

    def test_longer_path_does_not_excuse_a_prefix_match(self):
        ents = [*self.task(depends=[OPS1]), self.ops(f"copy the file to {MISSING}.bak")]
        got = self.check(ents)
        self.assertEqual(len(got), 1)
        self.assertIn(MISSING, got[0])

    def test_production_path_from_tasks_md_passes(self):
        # no injected key: check_stage reads TASKS.md (ops-N manual text, Depends) and the task files, as in production
        stage = os.path.join(self.ws, ".work", "s")
        os.makedirs(stage)
        _write(os.path.join(self.ws, "pipeline.json"), "{}")
        _write(
            os.path.join(stage, "TASKS.md"),
            "- task-1 build - review: wave W1\n- [ ] ops-1 manual: copy the file to out/data.csv\n- task-2 use - review: wave W2 - depends: ops-1\n\n## Graph\nwave 1: task-1, ops-1\nwave 2: task-2\n",
        )
        _write(os.path.join(stage, "task-1.md"), "# t1\nPredicted paths: src/a.py\n")
        _write(os.path.join(stage, "task-2.md"), f"# t2\nInputs: {MISSING}\n")
        _, probs = plan_check.check_stage(stage, repo=self.ws)
        self.assertEqual([p for p in probs if MISSING in p], [])
