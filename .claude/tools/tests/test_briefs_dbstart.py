"""The dev brief carries a `DB start: <cmd>` line when the workspace CLAUDE.md has a `db-start:` line, and nothing otherwise."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs
from pipeline.configuration import DB_START_PREFIX

START_CMD = "docker compose up -d db"


class _Run:
    def __init__(self, d):
        self.stage = "s"
        self.dir = d


class DbStartNoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="briefs-dbstart-")
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def _write_claude(self, text):
        with open(os.path.join(self.dir, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_db_start_line_is_passed_to_the_brief(self):
        self._write_claude(f"# Notes\n{DB_START_PREFIX} {START_CMD}\n")
        note = briefs.db_start_note(_Run(self.dir))
        self.assertIn(f"DB start: {START_CMD}", note)

    def test_absent_line_adds_nothing(self):
        self._write_claude("# Notes\nno start command here\n")
        self.assertEqual(briefs.db_start_note(_Run(self.dir)), "")

    def test_missing_claude_file_adds_nothing(self):
        self.assertEqual(briefs.db_start_note(_Run(self.dir)), "")

    def test_prefix_must_start_the_line(self):
        self._write_claude(f"  note: {DB_START_PREFIX} {START_CMD}\n")
        self.assertEqual(briefs.db_start_note(_Run(self.dir)), "")

    def test_dev_prompt_carries_the_db_start_line(self):
        # mutation check: prompt_for must call db_start_note; drop that call and this test fails
        self._write_claude(f"{DB_START_PREFIX} {START_CMD}\n")
        self.assertIn(f"DB start: {START_CMD}", self._dev_prompt())

    def test_dev_prompt_without_the_line_names_no_start_command(self):
        self._write_claude("# Notes\n")
        self.assertNotIn("DB start:", self._dev_prompt())

    def _dev_prompt(self):
        a = _Run(self.dir)
        task_file = os.path.join(self.dir, ".work", "s", "task-1.md")
        os.makedirs(os.path.dirname(task_file))
        with open(task_file, "w", encoding="utf-8") as fh:
            fh.write("Predicted paths: x.py\nPattern: x.py:1\nTest: true\n")
        with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}), mock.patch("pipeline.briefs.job_info", return_value=None):
            return briefs.prompt_for(a, {"id": "task-1"}, "dev", None, "x1")


if __name__ == "__main__":
    unittest.main()
