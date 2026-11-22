"""Synthetic tests for pipeline.json `gates`: parsing, first-red-gate stop, CI drift and its wiring."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import final_check as fc
from pipeline import plan_check
from pipeline.ci_drift import ci_run_lines, drift
from pipeline.configuration import ConfigError, project
from pipeline.extra_tools import main as xtools_main
from tests.support.tmpdirs import tracked_mkdtemp


def make(cfg, ci=None):
    d = tracked_mkdtemp()
    with open(os.path.join(d, "pipeline.json"), "w") as f:
        json.dump(cfg, f)
    if ci is not None:
        os.makedirs(os.path.join(d, ".github", "workflows"))
        with open(os.path.join(d, ".github", "workflows", "ci.yml"), "w") as f:
            f.write(ci)
    return d


CI = "jobs:\n  t:\n    steps:\n      - run: npm ci\n      - run: npm run lint\n      - run: echo hi\n      - run: |\n          npm test\n\n          make build\n"
G = [{"name": "lint", "cmd": "npm run lint"}, {"name": "test", "cmd": "npm test && make build"}]


class GatesConfigTest(unittest.TestCase):
    def test_full_test_key_raises(self):
        with self.assertRaises(ConfigError) as cm:
            project(make({"gates": G, "full_test": "make check"}))
        self.assertIn("gates", str(cm.exception))

    def test_validation_errors(self):
        for bad in ([{"name": "a", "cmd": "x"}, {"name": "a", "cmd": "y"}], [{"name": "a", "cmd": " "}], [{"cmd": "x"}], "x"):
            with self.assertRaises(ConfigError):
                project(make({"gates": bad}))


class GateRunTest(unittest.TestCase):
    def test_all_gates_run_and_report_names_every_red(self):
        cfg = {"gates": [{"name": "one", "cmd": "exit 0"}, {"name": "two", "cmd": "echo boom; exit 3"}, {"name": "three", "cmd": "echo bang; exit 4"}]}
        d = make(cfg)
        cfg = project(d)
        gates = fc.gate_names(cfg)
        res = fc.run_all(fc.commands(cfg, d), d, dict(os.environ))
        self.assertEqual([r[1] for r in res], [0, 3, 4])
        rep = fc.report(res, gates)
        self.assertIn("gate two: FAILED", rep)
        self.assertIn("gate three: FAILED", rep)
        self.assertIn("boom", rep)
        self.assertIn("bang", rep)

    def test_legacy_runs_all(self):
        res = fc.run_all(["exit 3", "exit 0"], tracked_mkdtemp(), dict(os.environ))
        self.assertEqual(len(res), 2)


class DriftTest(unittest.TestCase):
    def test_run_lines(self):
        self.assertEqual(ci_run_lines(CI), ["npm ci", "npm run lint", "echo hi", "npm test", "make build"])

    def test_multiline_shell_is_not_split_into_fragments(self):
        ci = (
            "steps:\n  - run: |\n      # comment line\n      npm run lint\n"
            '      if [ -n "$X" ]; then\n        echo set\n      else\n        echo unset\n      fi\n'
            "      npm test \\\n        && make build\n"
            "      cat <<EOF\n      body line\n      EOF\n"
        )
        self.assertEqual(ci_run_lines(ci), ["npm run lint", "npm test && make build", "cat <<EOF"])
        d = make({"gates": G}, ci)
        self.assertEqual(drift(d, project(d)), ["CI runs `cat <<EOF` (ci.yml) but no gate covers it"])

    def test_clean_when_gates_cover_ci(self):
        d = make({"gates": G}, CI)
        self.assertEqual(drift(d, project(d)), [])

    def test_missing_command_warned(self):
        d = make({"gates": G[:1]}, CI)
        w = drift(d, project(d))
        self.assertEqual(len(w), 2)
        self.assertIn("npm test", w[0])

    def test_disabled_no_workflows_and_legacy(self):
        d = make({"gates": G[:1], "ci_drift": False}, CI)
        self.assertEqual(drift(d, project(d)), [])
        d = make({"gates": G})
        self.assertEqual(drift(d, project(d)), [])
        d = make({}, CI)
        self.assertEqual(drift(d, project(d)), ["no gates configured; CI drift not checked"])

    def test_workflows_found_in_the_repo_subfolder(self):
        d = make({"repo": "app", "gates": [{"name": "lint", "cmd": "npm run lint"}]})
        os.makedirs(os.path.join(d, "app", ".github", "workflows"))
        with open(os.path.join(d, "app", ".github", "workflows", "ci.yml"), "w") as f:
            f.write("steps:\n  - run: npm run lint\n  - run: make build\n")
        self.assertEqual(drift(d, project(d)), ["CI runs `make build` (ci.yml) but no gate covers it"])


class DriftWiringTest(unittest.TestCase):
    def test_plan_check_drift_fails(self):
        d = make({"gates": G[:1]}, CI)
        stage = os.path.join(d, ".work", "s1")
        os.makedirs(stage)
        w = plan_check.ci_drift_warnings(stage)
        self.assertTrue(w and all(x.startswith("ci-drift:") for x in w))
        self.assertEqual(plan_check.errors_of(w), w)

    def test_tools_check_output_has_warning(self):
        d = make({"gates": G[:1]}, CI)
        out = os.path.join(d, "r.md")
        with (
            mock.patch("pipeline.extra_tools.check", return_value=([], [], [])),
            mock.patch("pipeline.extra_tools.ensure_proj"),
            self.assertRaises(SystemExit) as e,
        ):
            xtools_main(["check", "--proj", d, "--report", out])
        self.assertEqual(e.exception.code, 0)
        with open(out) as f:
            self.assertIn("warning: ci-drift:", f.read())


if __name__ == "__main__":
    unittest.main()
