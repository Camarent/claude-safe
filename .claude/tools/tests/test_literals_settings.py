"""configuration.literals: each named tool setting has its default, an env override, a pipeline.json override, and a bad value that falls back."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.configuration import literals
from pipeline.prices import PRICES

SWALLOWED = "pipeline.util.swallowed"


class ToolSettingTest(unittest.TestCase):
    def _resolve(self, name, env=None, block=None):
        """-> tool_setting(name) run from a fresh workspace holding pipeline.json `tool_settings` = block (None = no file)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if block is not None:
            with open(os.path.join(tmp.name, "pipeline.json"), "w", encoding="utf-8") as f:
                json.dump({literals.TOOL_SETTINGS_KEY: block}, f)
        var = literals.TOOL_ENV_PREFIX + name
        with mock.patch.dict(os.environ):
            os.environ.pop(var, None)
            if env is not None:
                os.environ[var] = env
            with mock.patch("os.getcwd", return_value=tmp.name):
                return literals.tool_setting(name)

    def _key(self, name):
        return literals._SETTINGS[name][1]

    def test_lsp_run_timeout(self):
        self.assertEqual(self._resolve("LSP_RUN_TIMEOUT_S"), 20)
        self.assertEqual(self._resolve("LSP_RUN_TIMEOUT_S", env="45"), 45)
        self.assertEqual(self._resolve("LSP_RUN_TIMEOUT_S", block={self._key("LSP_RUN_TIMEOUT_S"): 33}), 33)
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("LSP_RUN_TIMEOUT_S", env="-3"), 20)
        sw.assert_called_once()

    def test_ask_db_timeout(self):
        self.assertEqual(self._resolve("ASK_DB_TIMEOUT_S"), 60)
        self.assertEqual(self._resolve("ASK_DB_TIMEOUT_S", env="90"), 90)
        self.assertEqual(self._resolve("ASK_DB_TIMEOUT_S", block={self._key("ASK_DB_TIMEOUT_S"): 12}), 12)
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("ASK_DB_TIMEOUT_S", block={self._key("ASK_DB_TIMEOUT_S"): "soon"}), 60)
        sw.assert_called_once()

    def test_safeshell_timeout(self):
        self.assertEqual(self._resolve("SAFESHELL_TIMEOUT_S"), 60)
        self.assertEqual(self._resolve("SAFESHELL_TIMEOUT_S", env="120"), 120)
        self.assertEqual(self._resolve("SAFESHELL_TIMEOUT_S", block={self._key("SAFESHELL_TIMEOUT_S"): 15}), 15)
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("SAFESHELL_TIMEOUT_S", env="nan"), 60)
        sw.assert_called_once()

    def test_index_db_timeout(self):
        self.assertEqual(self._resolve("INDEX_DB_TIMEOUT_S"), 5)
        self.assertEqual(self._resolve("INDEX_DB_TIMEOUT_S", env="7"), 7)
        self.assertEqual(self._resolve("INDEX_DB_TIMEOUT_S", block={self._key("INDEX_DB_TIMEOUT_S"): 2}), 2)
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("INDEX_DB_TIMEOUT_S", block={self._key("INDEX_DB_TIMEOUT_S"): 0}), 5)
        sw.assert_called_once()

    def test_serve_connect_timeout(self):
        self.assertEqual(self._resolve("SERVE_CONNECT_TIMEOUT_S"), 0.5)
        self.assertEqual(self._resolve("SERVE_CONNECT_TIMEOUT_S", env="1.5"), 1.5)
        self.assertEqual(self._resolve("SERVE_CONNECT_TIMEOUT_S", block={self._key("SERVE_CONNECT_TIMEOUT_S"): 0.25}), 0.25)
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("SERVE_CONNECT_TIMEOUT_S", env="fast"), 0.5)
        sw.assert_called_once()

    def test_lsp_lock_timeout(self):
        self.assertEqual(self._resolve("LSP_LOCK_TIMEOUT_S"), 2)
        self.assertEqual(self._resolve("LSP_LOCK_TIMEOUT_S", env="4"), 4)
        self.assertEqual(self._resolve("LSP_LOCK_TIMEOUT_S", block={self._key("LSP_LOCK_TIMEOUT_S"): 1}), 1)
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("LSP_LOCK_TIMEOUT_S", block={self._key("LSP_LOCK_TIMEOUT_S"): True}), 2)
        sw.assert_called_once()

    def test_git_remote(self):
        self.assertEqual(self._resolve("GIT_REMOTE"), "origin")
        self.assertEqual(self._resolve("GIT_REMOTE", env="upstream"), "upstream")
        self.assertEqual(self._resolve("GIT_REMOTE", block={self._key("GIT_REMOTE"): "mirror"}), "mirror")
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("GIT_REMOTE", env="two words"), "origin")
        sw.assert_called_once()

    def test_prices_table(self):
        self.assertEqual(self._resolve("PRICES_TABLE"), PRICES)
        self.assertEqual(self._resolve("PRICES_TABLE", env="ignored"), PRICES)  # no env override for the table
        row = [["test-model", 1, 2, 0.1, 1.25]]
        self.assertEqual(self._resolve("PRICES_TABLE", block={self._key("PRICES_TABLE"): row}), (("test-model", 1, 2, 0.1, 1.25),))
        with mock.patch(SWALLOWED) as sw:
            self.assertEqual(self._resolve("PRICES_TABLE", block={self._key("PRICES_TABLE"): [["bad", "x", 1, 1, 1]]}), PRICES)
        sw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
