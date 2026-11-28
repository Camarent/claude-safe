"""The dev brief names the project's full-gate commands (pipeline.json `dev_gate_full_cmds`), forbids running them in the dev's own Bash call, and says a timed-out call is DONE, not BLOCKED."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs
from pipeline.configuration import GATE_TIMEOUT_S

TASK_ID = "t1"
FULL_CMDS = ["make full-check", "scripts/dev.sh cov"]
NEVER_PHRASE = "Never run a full gate command in your own Bash call"
TIMEOUT_PHRASE = "is never a reason to report BLOCKED: report DONE with the Test line result"


class _Run:
    def __init__(self, stage, d):
        self.stage = stage
        self.dir = d


class DevGateBriefTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dev-gate-brief-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.abspath(tmp.name)

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _dev_prompt(self, cfg):
        self._write("pipeline.json", json.dumps(cfg))
        self._write(os.path.join(".work", "s", TASK_ID + ".md"), "Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        wt = os.path.join(self.root, ".wt", "s", TASK_ID, "repo")
        info = {"branch": "job/s-" + TASK_ID, "repos": {"repo": wt}, "base": "dev"}
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=info):
            return briefs.prompt_for(_Run("s", self.root), {"id": TASK_ID}, "dev", None, "x1")

    def test_dev_brief_names_every_full_gate_command_of_the_config(self):
        prompt = self._dev_prompt({"dev_gate_full_cmds": FULL_CMDS})
        self.assertIn(NEVER_PHRASE, prompt)
        for cmd in FULL_CMDS:
            self.assertIn("`" + cmd + "`", prompt)
        self.assertIn("The driver runs the full gate after you report.", prompt)

    def test_dev_brief_takes_the_commands_from_the_setting_not_a_literal(self):
        prompt = self._dev_prompt({"dev_gate_full_cmds": ["only-this-gate"]})
        self.assertIn("`only-this-gate`", prompt)
        self.assertNotIn("scripts/dev.sh cov", prompt)

    def test_dev_brief_says_a_timed_out_call_is_done_not_blocked(self):
        prompt = self._dev_prompt({"dev_gate_full_cmds": FULL_CMDS})
        self.assertIn(f"agent tool timeout ({GATE_TIMEOUT_S} s) {TIMEOUT_PHRASE}", prompt)

    def test_dev_brief_keeps_timeout_rule_when_no_full_gate_is_configured(self):
        prompt = self._dev_prompt({"dev_gate_full_cmds": []})
        self.assertNotIn(NEVER_PHRASE, prompt)
        self.assertIn(TIMEOUT_PHRASE, prompt)

    def test_reviewer_prompt_has_no_full_gate_rule(self):
        self._write("pipeline.json", json.dumps({"dev_gate_full_cmds": FULL_CMDS}))
        self._write(os.path.join(".work", "s", TASK_ID + ".md"), "Predicted paths: x.py\nGoal: rename foo\nTest: true\n")
        wt = os.path.join(self.root, ".wt", "s", TASK_ID, "repo")
        info = {"branch": "job/s-" + TASK_ID, "repos": {"repo": wt}, "base": "dev"}
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=info):
            rev = briefs.prompt_for(_Run("s", self.root), {"id": TASK_ID}, "reviewer", None, "x2")
        self.assertNotIn(NEVER_PHRASE, rev)


if __name__ == "__main__":
    unittest.main()
