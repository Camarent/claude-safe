"""plan_toolblock.check_toolblock: a Test/Done that runs a JS tool whose probe fails is `tool-block:` (unless the task depends on an ops-N row); `knip --production` with no knip entry is `knip-prod:`. Temp project, fake probe; no JS project or absent tool = no check."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_problem, plan_toolblock, tool_probe

M25_REASON = "madge peer typescript 5 vs project 7"
TID = "task-1"
TOOL_BLOCK = "tool-block:"
KNIP_PROD = "knip-prod:"
BAD = tool_probe.Probe(False, M25_REASON)
GOOD = tool_probe.Probe(True, "")


def present(_tool):
    return True


class ToolBlockTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.package_json({"devDependencies": {"typescript": "^7.0.0"}})

    def package_json(self, data):
        with open(os.path.join(self.root, "package.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def task(self, name, body):
        with open(os.path.join(self.d, name + ".md"), "w", encoding="utf-8") as f:
            f.write(body)

    def ents(self, depends=()):
        return [{"id": "task-1", "depends": list(depends)}]

    def probe(self, result):
        calls = []

        def fake(tool, proj):
            calls.append((tool, proj))
            return result

        return fake, calls

    def check(self, result=BAD, depends=(), installed=present):
        fake, calls = self.probe(result)
        probs = plan_toolblock.check_toolblock(self.d, self.ents(depends), probe=fake, installed=installed)
        return probs, calls

    def test_m25_madge_probe_not_ok_is_tool_block(self):
        self.task("task-1", "# T1\nPredicted paths: a.js\nTest: npx madge --circular src\nDone: C1: no cycles\n")
        probs, calls = self.check(BAD)
        self.assertEqual(probs, [f"{TOOL_BLOCK} {TID} uses madge: {M25_REASON}"])
        self.assertEqual(calls, [("madge", self.root)])
        self.assertEqual(plan_problem.Problem.parse(probs[0]).level, plan_problem.ERROR)

    def test_tool_named_in_done_is_checked(self):
        self.task("task-1", "# T1\nTest: npm test\nDone: C1: eslint runs clean\n")
        probs, _ = self.check(BAD)
        self.assertEqual(probs, [f"{TOOL_BLOCK} {TID} uses eslint: {M25_REASON}"])

    def test_probe_ok_passes(self):
        self.task("task-1", "# T1\nTest: npx madge --circular src\nDone: C1: no cycles\n")
        self.assertEqual(self.check(GOOD)[0], [])

    def test_ops_dependency_exempts_tool_block(self):
        self.task("task-1", "# T1\nTest: npx madge --circular src\nDone: C1: no cycles\n")
        self.assertEqual(self.check(BAD, depends=("ops-1",))[0], [])

    def test_word_inside_longer_word_or_file_name_is_no_tool_use(self):
        self.task("task-1", "# T1\nTest: pytest tests/madge_report.py\nDone: C1: madgeish output, eslint-plugin loads, madge.py runs\n")
        self.assertEqual(self.check(BAD)[0], [])

    def test_knip_production_without_entry_is_knip_prod(self):
        self.task("task-1", "# T1\nTest: npx knip --production\nDone: C1: no unused files\n")
        self.package_json({"knip": {"ignore": ["x.js"]}})
        probs, _ = self.check(GOOD)
        self.assertEqual(probs, [f"{KNIP_PROD} {TID} runs knip --production: knip config has no entry key"])
        self.assertEqual(plan_problem.Problem.parse(probs[0]).level, plan_problem.ERROR)

    def test_knip_production_with_entry_passes(self):
        self.task("task-1", "# T1\nTest: npx knip --production\nDone: C1: no unused files\n")
        self.package_json({"knip": {"entry": ["src/index.ts"]}})
        self.assertEqual(self.check(GOOD)[0], [])

    def test_no_js_project_no_check(self):
        self.task("task-1", "# T1\nTest: npx madge --circular src\nDone: C1: no cycles\n")
        os.remove(os.path.join(self.root, "package.json"))
        self.assertEqual(self.check(BAD)[0], [])

    def test_absent_tool_no_check(self):
        self.task("task-1", "# T1\nTest: npx madge --circular src\nDone: C1: no cycles\n")
        self.assertEqual(self.check(BAD, installed=lambda _tool: False)[0], [])

    def test_kinds_are_errors(self):
        for kind in (TOOL_BLOCK, KNIP_PROD):
            self.assertEqual(plan_problem.Problem.parse(f"{kind} {TID}: x").level, plan_problem.ERROR)

    def test_plan_check_wires_the_rule(self):
        self.task("task-1", "# T1\nPredicted paths: a.js\nTest: npx madge --circular src\nDone: C1: no cycles\n")
        with open(os.path.join(self.d, "TASKS.md"), "w", encoding="utf-8") as f:
            f.write("## Tasks\n- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n\n## Graph\nwave 1: task-1\n")
        fake, _ = self.probe(BAD)

        def with_fake_probe(d, ents):
            return plan_toolblock.check_toolblock(d, ents, probe=fake, installed=present)

        with mock.patch.object(plan_check, "check_toolblock", side_effect=with_fake_probe) as rule:
            probs = plan_check.check_stage(self.d, repo=self.root)[1]
        rule.assert_called_once()
        self.assertIn(f"{TOOL_BLOCK} {TID} uses madge: {M25_REASON}", probs)
