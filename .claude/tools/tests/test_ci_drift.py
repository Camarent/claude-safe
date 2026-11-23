"""CI drift: a CI run line no gate covers is an error in cs-plan check; this repo's gates cover its own ci.yml."""

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.ci_drift import ci_commands, drift
from pipeline.configuration import project
from tests.support.tmpdirs import tracked_mkdtemp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CI = "jobs:\n  t:\n    steps:\n      - run: npm ci\n      - run: npm run lint\n      - run: npm test\n"
G = [{"name": "lint", "cmd": "npm run lint"}, {"name": "test", "cmd": "npm test"}]


def make(cfg, ci):
    d = tracked_mkdtemp()
    with open(os.path.join(d, "pipeline.json"), "w") as f:
        json.dump(cfg, f)
    os.makedirs(os.path.join(d, ".github", "workflows"))
    with open(os.path.join(d, ".github", "workflows", "ci.yml"), "w") as f:
        f.write(ci)
    return d


def repo_cfg():
    with open(os.path.join(REPO, "pipeline.json"), encoding="utf-8") as f:
        return json.load(f)


class CiDriftIsErrorTest(unittest.TestCase):
    def test_missing_ci_command_is_error_in_check(self):
        d = make({"gates": G[:1]}, CI)
        stage = os.path.join(d, ".work", "s1")
        os.makedirs(stage)
        probs = plan_check.ci_drift_warnings(stage)
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith("ci-drift: CI runs `npm test`"))
        self.assertEqual(plan_check.errors_of(probs), probs)


CI_ONLY = (
    "set -euo pipefail",
    "cd web",
    "export FOO=bar",
    "NODE_ENV=production",
    "./scripts/deploy ${{ secrets.TOKEN }}",
    'test -n "$RUNNER_TEMP"',
    'test -n "$GITHUB_SHA"',
    "./scripts/collect.sh $RUNNER_OS",
)


class CiOnlyLinesTest(unittest.TestCase):
    def test_ci_only_lines_are_not_drift(self):
        # Each line is never run locally: GitHub-only vars, secrets, shell settings.
        ci = "jobs:\n  t:\n    steps:\n" + "".join(f"      - run: {c}\n" for c in CI_ONLY)
        d = make({"gates": G}, ci)
        self.assertEqual(ci_commands(d, {"gates": G}), [])
        self.assertEqual(drift(d, project(d)), [])

    def test_decoy_line_is_still_reported(self):
        # Not never-local: no GitHub var, no secret, and a shell setting followed by a separator is a real command.
        decoys = ["./dojo build", "cd web && pytest tests", "set -e; ./scripts/deploy", "export X=1 && npm test", "./dojo test $MY_RUNNER_X"]
        ci = "jobs:\n  t:\n    steps:\n" + "".join(f"      - run: {c}\n" for c in decoys)
        d = make({"gates": G}, ci)
        self.assertEqual(ci_commands(d, {"gates": G}), decoys)


class RepoGatesCoverCiTest(unittest.TestCase):
    def test_repo_ci_has_no_drift(self):
        self.assertEqual(drift(REPO, project(REPO)), [])

    def test_dropping_one_gate_is_drift(self):
        cfg = copy.deepcopy(repo_cfg())
        cfg["gates"] = [g for g in cfg["gates"] if g["name"] != "ccn"]
        with open(os.path.join(REPO, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            d = make(cfg, f.read())
        self.assertEqual(drift(d, project(d)), ["CI runs `scripts/dev.sh ccn` (ci.yml) but no gate covers it"])
