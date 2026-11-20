"""orch retry --addendum: appends one dated block to the step's task file and sets the note; script steps refuse it; without the flag nothing changes."""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import cli_orch  # noqa: E402

HEADING_RE = re.compile(r"^## ADDENDUM \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$", re.M)


class RetryAddendum(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/.work/s/task-1.md", "w", encoding="utf-8") as f:
            f.write("Test: true\n")
        with open(f"{self.t}/.work/s/orch.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"ev": "report", "task": "task-1", "id": "a1", "role": "dev", "verdict": "BLOCKED", "reason": "x"}) + "\n")

    def task(self):
        with open(f"{self.t}/.work/s/task-1.md", encoding="utf-8") as f:
            return f.read()

    def log(self):
        with open(f"{self.t}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def retry(self, step="task-1", note="", addendum=""):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step=step, note=note, from_step="", addendum=addendum)
        with (
            mock.patch.object(cli_orch, "_refresh_job"),
            mock.patch.object(cli_orch, "_reset_fix_chain"),
            mock.patch.object(cli_orch, "_blocked_dev_is_green", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli_orch.cmd_reset(a)

    def test_addendum_appended_once_and_sets_note(self):
        self.retry(note="fix the import", addendum="Use the new helper, not the old one.")
        txt = self.task()
        self.assertTrue(txt.startswith("Test: true\n"))
        self.assertEqual(len(HEADING_RE.findall(txt)), 1)
        self.assertIn("Use the new helper, not the old one.", txt)
        ev = [e for e in self.log() if e["ev"] == "retry"][-1]
        self.assertEqual(ev["note"], "fix the import\nUse the new helper, not the old one.")

    def test_second_addendum_appends_again(self):
        self.retry(addendum="first hint")
        self.retry(addendum="second hint")
        txt = self.task()
        self.assertEqual(len(HEADING_RE.findall(txt)), 2)
        self.assertLess(txt.index("first hint"), txt.index("second hint"))

    def test_script_step_refuses_and_changes_nothing(self):
        before = self.task()
        log_before = self.log()
        with self.assertRaises(SystemExit) as cm:
            self.retry(step="final-test", addendum="anything")
        self.assertIn("no task file", str(cm.exception))
        self.assertEqual(self.task(), before)
        self.assertEqual(self.log(), log_before)

    def test_note_unchanged_without_flag(self):
        self.retry(note="only the note")
        self.assertNotIn("ADDENDUM", self.task())
        ev = [e for e in self.log() if e["ev"] == "retry"][-1]
        self.assertEqual(ev["note"], "only the note")


if __name__ == "__main__":
    unittest.main()
