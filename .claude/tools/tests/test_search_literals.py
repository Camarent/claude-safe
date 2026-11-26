"""The search/ timeouts read their named tool settings: env CS_<NAME> reaches the call, the default holds when unset."""

import io
import os
import sys
import tempfile
import unittest
from collections import OrderedDict
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code, lsp, lsp_pool


def _env(**values):
    """Patch os.environ with the given CS_* values (none when empty); the patch restores the real environment on exit."""
    return mock.patch.dict(os.environ, values)


class SearchTimeoutSettings(unittest.TestCase):
    def setUp(self):
        # no pipeline.json tool_settings here: the defaults and env overrides are what these tests read
        patcher = mock.patch("pipeline.configuration.literals.project", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lsp_run_timeout_reaches_subprocess(self):
        ok = SimpleNamespace(returncode=0, stderr="")
        with _env(CS_LSP_RUN_TIMEOUT_S="7"), mock.patch("search.lsp.subprocess.run", return_value=ok) as run, redirect_stdout(io.StringIO()):
            lsp.not_checked("x.py", "py", "test")
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_lsp_run_timeout_default_when_unset(self):
        ok = SimpleNamespace(returncode=0, stderr="")
        with _env(), mock.patch("search.lsp.subprocess.run", return_value=ok) as run, redirect_stdout(io.StringIO()):
            os.environ.pop("CS_LSP_RUN_TIMEOUT_S", None)
            lsp.not_checked("x.py", "py", "test")
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_ask_db_timeout_reaches_sqlite_connect(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            _env(CS_ASK_DB_TIMEOUT_S="9"),
            mock.patch.object(ask_code, "cache_path") as path_of,
            mock.patch("search.ask_code.sqlite3.connect") as connect,
        ):
            path_of.return_value = os.path.join(tmp, "idx", "v.db")
            ask_code.vcache(tmp)
        self.assertEqual(connect.call_args.kwargs["timeout"], 9)

    def test_ask_db_timeout_default_when_unset(self):
        with (
            _env(),
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(ask_code, "cache_path") as path_of,
            mock.patch("search.ask_code.sqlite3.connect") as connect,
        ):
            os.environ.pop("CS_ASK_DB_TIMEOUT_S", None)
            path_of.return_value = os.path.join(tmp, "idx", "v.db")
            ask_code.vcache(tmp)
        self.assertEqual(connect.call_args.kwargs["timeout"], 60)

    def _pool_with_entry(self):
        entry = SimpleNamespace(lock=mock.Mock(), close=mock.Mock())
        pool = lsp_pool.Pool()
        pool.entries = OrderedDict([(("py", "/r"), entry)])
        return pool, entry

    def test_lsp_lock_timeout_reaches_lock_acquire(self):
        pool, entry = self._pool_with_entry()
        with _env(CS_LSP_LOCK_TIMEOUT_S="3"):
            pool.close_all()
        self.assertEqual(entry.lock.acquire.call_args.kwargs["timeout"], 3)

    def test_lsp_lock_timeout_default_when_unset(self):
        pool, entry = self._pool_with_entry()
        with _env():
            os.environ.pop("CS_LSP_LOCK_TIMEOUT_S", None)
            pool.close_all()
        self.assertEqual(entry.lock.acquire.call_args.kwargs["timeout"], 2)


if __name__ == "__main__":
    unittest.main()
