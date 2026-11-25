import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import denial_log
from pipeline.configuration import ENV_AGENT_KIND, ENV_DENIAL_SESSION, ENV_SCRATCH_DIR


class DenialLogTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.scratch = os.path.realpath(t.name)
        self.log = os.path.join(self.scratch, denial_log.DENIALS_FILE)
        # the shell may set PIPELINE_AGENT_KIND (an agent session does): every test starts without it; patch.dict restores the env
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(ENV_AGENT_KIND, None)

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
                {"ts": 12.5, "rule": "guard", "command": "rm -rf /x", "cwd": "/w/job", "agent": "", "session": ""},
                {"ts": 13.0, "rule": "main_checkout", "command": "/main/a.py", "cwd": "/w/job", "agent": "", "session": ""},
            ],
        )

    def test_session_token_from_env(self):
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: self.scratch, ENV_DENIAL_SESSION: "tok"}):
            denial_log.append("guard", "ls", "/w/job", now=1.0)
        self.assertEqual(self.read_lines()[0]["session"], "tok")

    def test_agent_kind_from_env(self):
        with mock.patch.dict(os.environ, {ENV_SCRATCH_DIR: self.scratch, ENV_AGENT_KIND: "dev"}):
            denial_log.append("guard", "ls", "/w", now=1.0)
        self.assertEqual(self.read_lines()[0]["agent"], "dev")

    def test_rows_reads_file_in_order_and_skips_junk(self):
        with open(self.log, "w", encoding="utf-8") as f:
            f.write('{"rule": "guard", "command": "ls"}\nnot json\n5\n{"rule": "sed", "command": "sed -i"}\n')
        self.assertEqual(denial_log.rows(self.scratch), [{"rule": "guard", "command": "ls"}, {"rule": "sed", "command": "sed -i"}])

    def test_rows_missing_file_is_empty(self):
        self.assertEqual(denial_log.rows(self.scratch), [])

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
