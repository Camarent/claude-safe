"""Swallowed errors in the top-level tools now leave a `swallowed in <where>` line on stderr (pipeline.util.swallowed)."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docs_index
import index_step
import ops_steps
import servectl
from pipeline import pr_mode
from pipeline.configuration import SERVE_STATE_SUFFIX
from pipeline.util import swallowed


class SwallowedLogsTest(unittest.TestCase):
    def tmpdir(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name

    def capture(self, fn, *args, **kwargs):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = fn(*args, **kwargs)
        return out, err.getvalue()

    def test_swallowed_writes_one_stderr_line(self):
        _, err = self.capture(swallowed, "where.x", OSError("boom"))
        self.assertEqual(err, "swallowed in where.x: OSError: boom\n")

    def test_docs_index_scan_logs_unreadable_file(self):
        root = self.tmpdir()
        os.mkdir(os.path.join(root, "d.md"))  # a directory: open() raises IsADirectoryError
        (lines, symbols), err = self.capture(docs_index.scan, root, "d.md")
        self.assertEqual((lines, symbols), (0, []))
        self.assertIn("swallowed in docs_index.scan: ", err)

    def test_index_step_vacuum_logs_missing_db(self):
        missing = os.path.join(self.tmpdir(), "absent.db")
        out, err = self.capture(index_step.vacuum_bloated, missing)
        self.assertEqual(out, "")
        self.assertIn("swallowed in index_step.vacuum_bloated: ", err)

    def test_pr_mode_run_logs_missing_cwd(self):
        missing = os.path.join(self.tmpdir(), "absent")
        out, err = self.capture(pr_mode._run, ["true"], missing)
        self.assertIsNone(out)
        self.assertIn("swallowed in pr_mode._run: ", err)

    def test_ops_steps_is_public_logs_gh_failure(self):
        repo = self.tmpdir()  # no CLAUDE.md: the gh call decides, and it fails here
        with mock.patch.object(ops_steps.subprocess, "run", side_effect=OSError("gh missing")):
            out, err = self.capture(ops_steps._is_public, repo)
        self.assertIsNone(out)
        self.assertIn("swallowed in ops_steps._is_public: OSError: gh missing", err)

    def test_servectl_load_records_logs_bad_state_file(self):
        d = self.tmpdir()
        with open(os.path.join(d, "bad" + SERVE_STATE_SUFFIX), "w", encoding="utf-8") as f:
            f.write("{not json")
        out, err = self.capture(servectl.load_records, d)
        self.assertEqual(out, [])
        self.assertIn("swallowed in servectl.load_records: ", err)


if __name__ == "__main__":
    unittest.main()
