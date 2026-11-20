"""util.read_jsonl / append_jsonl: a truncated or blank line must not brick the readers."""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import util
from tests.support.tmpdirs import tracked_mkdtemp


class ReadJsonlTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.path = os.path.join(self.d, "orch.jsonl")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_truncated_last_line_is_skipped_with_warning(self):
        self.write('{"ev": "a"}\n{"ev": "b"}\n{"ev": "c"\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = util.read_jsonl(self.path)
        self.assertEqual(rows, [{"ev": "a"}, {"ev": "b"}])
        self.assertIn(self.path, err.getvalue())
        self.assertIn("line 3", err.getvalue())

    def test_blank_lines_are_skipped_silently(self):
        self.write('\n{"ev": "a"}\n\n   \n{"ev": "b"}\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = util.read_jsonl(self.path)
        self.assertEqual(rows, [{"ev": "a"}, {"ev": "b"}])
        self.assertEqual(err.getvalue(), "")

    def test_missing_file_is_empty(self):
        self.assertEqual(util.read_jsonl(os.path.join(self.d, "none.jsonl")), [])


class AppendJsonlTest(unittest.TestCase):
    def test_bare_filename_in_cwd(self):
        here = os.getcwd()
        d = tracked_mkdtemp()
        os.chdir(d)
        try:
            util.append_jsonl("bare.jsonl", {"ev": "x"})
        finally:
            os.chdir(here)
        with open(os.path.join(d, "bare.jsonl"), encoding="utf-8") as f:
            self.assertEqual([json.loads(line) for line in f], [{"ev": "x"}])

    def test_torn_line_does_not_glue_next_record(self):
        d = tracked_mkdtemp()
        path = os.path.join(d, "orch.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"ev": "a"}\n{"ev": "b"')  # torn write: no trailing newline
        util.append_jsonl(path, {"ev": "c"})
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], '{"ev": "a"}')
        self.assertEqual(lines[1], '{"ev": "b"')
        self.assertEqual(json.loads(lines[2]), {"ev": "c"})


if __name__ == "__main__":
    unittest.main()
