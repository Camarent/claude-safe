"""The tool call sites of safeshell, index_step, servectl and pipeline.pr_mode read their named tool setting: the default is unchanged and a
`CS_<NAME>` env override reaches the call."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import index_step
import servectl
from localmodel import safeshell
from pipeline import pr_mode
from pipeline.configuration import literals


class ToolLiteralSitesTest(unittest.TestCase):
    def _with_setting(self, name, value, fn):
        """-> fn() run with env `CS_<name>` set to VALUE (None = unset) from a fresh cwd without pipeline.json."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        var = literals.TOOL_ENV_PREFIX + name
        with mock.patch.dict(os.environ):
            os.environ.pop(var, None)
            if value is not None:
                os.environ[var] = value
            with mock.patch("os.getcwd", return_value=tmp.name):
                return fn()

    def test_safeshell_run_timeout(self):
        done = subprocess.CompletedProcess(["bash"], 0, stdout="ok", stderr="")

        def call():
            with mock.patch.object(safeshell.subprocess, "run", return_value=done) as run:
                safeshell.run("echo ok", tempfile.gettempdir())
            return run.call_args.kwargs["timeout"]

        self.assertEqual(self._with_setting("SAFESHELL_TIMEOUT_S", None, call), 60)
        self.assertEqual(self._with_setting("SAFESHELL_TIMEOUT_S", "120", call), 120)

    def test_index_db_connect_timeout(self):
        def call():
            with (
                mock.patch.object(index_step.os.path, "getsize", return_value=index_step.VACUUM_MIN_BYTES),
                mock.patch.object(index_step.sqlite3, "connect", side_effect=index_step.sqlite3.Error("busy")) as connect,
            ):
                index_step.vacuum_bloated("index.db")
            return connect.call_args.kwargs["timeout"]

        self.assertEqual(self._with_setting("INDEX_DB_TIMEOUT_S", None, call), 5)
        self.assertEqual(self._with_setting("INDEX_DB_TIMEOUT_S", "7", call), 7)

    def test_serve_connect_timeout(self):
        def call():
            with mock.patch.object(servectl.socket, "create_connection", side_effect=OSError("refused")) as conn:
                servectl.port_open(8000)
            return conn.call_args.kwargs["timeout"]

        self.assertEqual(self._with_setting("SERVE_CONNECT_TIMEOUT_S", None, call), 0.5)
        self.assertEqual(self._with_setting("SERVE_CONNECT_TIMEOUT_S", "1.5", call), 1.5)

    def test_git_remote_origin_url(self):
        def call():
            with mock.patch.object(pr_mode, "_git", return_value=None) as git:
                pr_mode.origin_url("repo")
            return git.call_args.args[-1]

        self.assertEqual(self._with_setting("GIT_REMOTE", None, call), "origin")
        self.assertEqual(self._with_setting("GIT_REMOTE", "upstream", call), "upstream")


if __name__ == "__main__":
    unittest.main()
