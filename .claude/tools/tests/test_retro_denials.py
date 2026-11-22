"""retro/denials.py --log: rows of a denials.jsonl counted by rule (temp file, no transcripts)."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import denials


class DenialLogRulesTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.log = os.path.join(os.path.realpath(t.name), "denials.jsonl")
        rows = [
            {"ts": 1.0, "rule": "sed", "command": "sed -n 's/a/b/p' f", "cwd": "/w"},
            {"ts": 2.0, "rule": "cd_git", "command": "cd /x && git log", "cwd": "/w"},
            {"ts": 3.0, "rule": "sed", "command": "sed -n 1p f", "cwd": "/w"},
        ]
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")

    def test_rows_are_the_logged_objects(self):
        rows = denials.log_rows(self.log)
        self.assertEqual(len(rows), 3)  # the bad line is skipped
        self.assertEqual(rows[1]["rule"], "cd_git")

    def test_counted_by_rule_most_first(self):
        self.assertEqual(denials.by_rule(denials.log_rows(self.log)), [{"rule": "sed", "count": 2}, {"rule": "cd_git", "count": 1}])

    def test_missing_log_has_no_rows(self):
        self.assertEqual(denials.log_rows(self.log + ".none"), [])

    def test_main_log_prints_counts(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(denials.main(["--log", self.log]), 0)
        self.assertIn("2  sed", out.getvalue())
        self.assertIn("1  cd_git", out.getvalue())

    def test_main_log_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            denials.main(["--log", self.log, "--json"])
        self.assertEqual(json.loads(out.getvalue())[0], {"rule": "sed", "count": 2})


if __name__ == "__main__":
    unittest.main()
