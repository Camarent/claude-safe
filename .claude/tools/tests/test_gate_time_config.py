"""Gate settings: gate_time_warn_s, gates_merge (empty), merge_only_tests, dev_gate_full_cmds and dev_gate_repeat_max read through configuration.project()."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import (
    DEV_GATE_FULL_CMDS_DEFAULT,
    DEV_GATE_FULL_CMDS_KEY,
    DEV_GATE_REPEAT_MAX_DEFAULT,
    DEV_GATE_REPEAT_MAX_KEY,
    GATE_TIME_WARN_DEFAULT,
    GATE_TIME_WARN_KEY,
    MERGE_ONLY_TESTS_DEFAULT,
    MERGE_ONLY_TESTS_KEY,
    PROJECT_DEFAULTS,
    project,
)
from tests.support.tmpdirs import tracked_mkdtemp

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(TOOLS))
PIPELINE_JSON = os.path.join(REPO, "pipeline.json")
# literals the gate settings must not carry in the dev gate code: their values come from configuration
DEV_GATE_LITERALS = ("test_lsp_e2e.py", "scripts/dev.sh cov", "scripts/dev.sh gate")


def settings_of(pipeline):
    """-> project() of a fresh workspace whose pipeline.json holds `pipeline`."""
    d = tracked_mkdtemp()
    with open(os.path.join(d, "pipeline.json"), "w", encoding="utf-8") as f:
        json.dump(pipeline, f)
    return project(d)


class TestDefaults(unittest.TestCase):
    def test_gate_time_warn_default_per_kind(self):
        self.assertEqual(PROJECT_DEFAULTS[GATE_TIME_WARN_KEY], {"unit": 120, "merge": 300, "full": 540})
        self.assertEqual(GATE_TIME_WARN_DEFAULT, {"unit": 120, "merge": 300, "full": 540})

    def test_merge_only_and_dev_gate_defaults(self):
        self.assertEqual(MERGE_ONLY_TESTS_DEFAULT, [])
        self.assertEqual(DEV_GATE_FULL_CMDS_DEFAULT, ["scripts/dev.sh cov", "scripts/dev.sh gate"])
        self.assertEqual(DEV_GATE_REPEAT_MAX_DEFAULT, 1)

    def test_empty_workspace_gets_the_defaults(self):
        cfg = project(tracked_mkdtemp())
        self.assertEqual(cfg[GATE_TIME_WARN_KEY], GATE_TIME_WARN_DEFAULT)
        self.assertEqual(cfg[MERGE_ONLY_TESTS_KEY], MERGE_ONLY_TESTS_DEFAULT)
        self.assertEqual(cfg[DEV_GATE_FULL_CMDS_KEY], DEV_GATE_FULL_CMDS_DEFAULT)
        self.assertEqual(cfg[DEV_GATE_REPEAT_MAX_KEY], DEV_GATE_REPEAT_MAX_DEFAULT)


class TestOverrides(unittest.TestCase):
    def test_pipeline_json_overrides_gate_time_warn(self):
        cfg = settings_of({GATE_TIME_WARN_KEY: {"unit": 30, "merge": 60, "full": 90}})
        self.assertEqual(cfg[GATE_TIME_WARN_KEY], {"unit": 30, "merge": 60, "full": 90})

    def test_pipeline_json_overrides_merge_only_dev_gate_and_repeat(self):
        cfg = settings_of({MERGE_ONLY_TESTS_KEY: [], DEV_GATE_FULL_CMDS_KEY: ["make all"], DEV_GATE_REPEAT_MAX_KEY: 3})
        self.assertEqual(cfg[MERGE_ONLY_TESTS_KEY], [])
        self.assertEqual(cfg[DEV_GATE_FULL_CMDS_KEY], ["make all"])
        self.assertEqual(cfg[DEV_GATE_REPEAT_MAX_KEY], 3)


class TestRepoGates(unittest.TestCase):
    def setUp(self):
        with open(PIPELINE_JSON, encoding="utf-8") as f:
            self.repo = json.load(f)

    def test_gates_keeps_its_four_unit_gates(self):
        self.assertEqual([g["name"] for g in self.repo["gates"]], ["check", "dead", "sec", "ccn"])

    def test_gates_final_is_unchanged_cov(self):
        self.assertEqual(self.repo["gates_final"], [{"name": "cov", "cmd": "scripts/dev.sh cov"}])

    def test_gates_merge_is_empty(self):
        self.assertEqual(self.repo["gates_merge"], [])


class TestNoDevGateLiterals(unittest.TestCase):
    def test_ptest_and_dev_gate_audit_carry_no_literal(self):
        for name in ("ptest.py", "dev_gate_audit.py"):
            for root, _dirs, files in os.walk(TOOLS):
                if name not in files:
                    continue
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    text = f.read()
                for literal in DEV_GATE_LITERALS:
                    self.assertNotIn(literal, text, f"{name} holds the literal {literal!r}")


if __name__ == "__main__":
    unittest.main()
