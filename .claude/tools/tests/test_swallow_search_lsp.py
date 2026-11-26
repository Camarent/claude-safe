"""Best-effort LSP paths that used to swallow errors silently now leave one stderr line (pipeline.util.swallowed)."""

import contextlib
import io
import os
import sys
import unittest
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import lsp_client, lsp_pool


class CloseLogsTest(unittest.TestCase):
    def test_close_logs_when_shutdown_fails(self):
        c = lsp_client.Client.__new__(lsp_client.Client)  # no server process: only the fields close() reads
        c.p = mock.Mock()
        err = io.StringIO()
        with mock.patch.object(c, "req", side_effect=RuntimeError("server gone")), contextlib.redirect_stderr(err):
            c.close()  # must not raise
        self.assertIn("swallowed in lsp_client.close: RuntimeError: server gone", err.getvalue())
        c.p.kill.assert_called_once()


class DrainLogsTest(unittest.TestCase):
    def test_drain_logs_when_client_is_dead(self):
        pool = lsp_pool.Pool()
        e = lsp_pool.Entry()
        client: Any = mock.Mock()  # Entry.client is typed None until a Client is set; the fake stands in for it
        client.drain.side_effect = OSError("pipe gone")
        e.client = client
        pool.entries["py"] = e
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pool.drain()  # must not raise
        self.assertIn("swallowed in lsp_pool.drain: OSError: pipe gone", err.getvalue())
        self.assertFalse(e.lock.locked())


if __name__ == "__main__":
    unittest.main()
