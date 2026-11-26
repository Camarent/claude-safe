"""The two swallowed errors in search/ask_code.py that now leave one stderr line (swallowed(where, exc)) and keep their return value."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code


class SwallowLogsTest(unittest.TestCase):
    def test_vcache_unwritable_path_logs_and_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")
            open(blocker, "w", encoding="utf-8").close()  # a file where the cache dir should be: makedirs fails
            err = io.StringIO()
            with mock.patch.object(ask_code, "cache_path", return_value=os.path.join(blocker, "vec.sqlite")), contextlib.redirect_stderr(err):
                self.assertIsNone(ask_code.vcache(tmp))
            self.assertIn("swallowed in ask_code.vcache: ", err.getvalue())

    def test_store_chunks_unreadable_file_logs_and_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertFalse(ask_code._store_chunks(None, tmp, "gone.py", {}))
            self.assertIn("swallowed in ask_code.store_chunks: ", err.getvalue())


if __name__ == "__main__":
    unittest.main()
