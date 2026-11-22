import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import denial_log
from pipeline.configuration import ENV_SCRATCH_DIR


class DenialLogTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.scratch = os.path.realpath(t.name)
        self.log = os.path.join(self.scratch, denial_log.DENIALS_FILE)

    def read_lines(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(x) for x in f.read().splitlines()]

    def test_appends_one_json_line_with_fields(self):
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: self.scratch}):
            denial_log.append("guard", "rm -rf /x", "/w/job", now=12.5)
            denial_log.append("main_checkout", "/main/a.py", "/w/job", now=13.0)
        self.assertEqual(
            self.read_lines(),
            [
                {"ts": 12.5, "rule": "guard", "command": "rm -rf /x", "cwd": "/w/job"},
                {"ts": 13.0, "rule": "main_checkout", "command": "/main/a.py", "cwd": "/w/job"},
            ],
        )

    def test_command_cut_to_200_chars(self):
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: self.scratch}):
            denial_log.append("guard", "x" * 500, "/w", now=1.0)
        self.assertEqual(len(self.read_lines()[0]["command"]), denial_log.COMMAND_CHARS)

    def test_unset_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_SCRATCH_DIR, None)
            denial_log.append("guard", "ls", "/w", now=1.0)
        self.assertEqual(os.listdir(self.scratch), [])

    def test_empty_is_noop(self):
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: ""}):
            denial_log.append("guard", "ls", "/w", now=1.0)
        self.assertEqual(os.listdir(self.scratch), [])


if __name__ == "__main__":
    unittest.main()
