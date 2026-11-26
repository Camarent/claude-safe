"""The JSONL readers keep their behaviour behind util.read_jsonl / util.parse_jsonl."""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_parsers
from pipeline import util
from retro import retro_numbers
from tests.support.tmpdirs import tracked_mkdtemp

BODY = b'{"a": 1}\n[1, 2]\n"s"\nnot json\n\n{"b": "\xff"}\n{"c": 3'


class JsonlReadersTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tracked_mkdtemp(), "x.jsonl")
        with open(self.path, "wb") as f:
            f.write(BODY)

    def test_retro_dicts_only_and_tolerant_of_bad_bytes(self):
        self.assertEqual(retro_numbers._json_lines(self.path), [{"a": 1}, {"b": "�"}])

    def test_audit_json_lines_only_brace_lines(self):
        text = 'warn\n  {"x": 1}\n{bad\n[1]\n{"y": 2}\n'
        self.assertEqual(list(audit_parsers.json_lines(text)), [{"x": 1}, {"y": 2}])

    def test_read_jsonl_warns_and_keeps_objects_by_default_any_value_on_request(self):
        with open(self.path, "wb") as f:
            f.write(b'{"a": 1}\n[1]\nnope\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(util.read_jsonl(self.path), [{"a": 1}])
            self.assertEqual(util.read_jsonl(self.path, dicts_only=False), [{"a": 1}, [1]])
        self.assertIn("skipped 1 unreadable", err.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(util.read_jsonl(os.path.join(os.path.dirname(self.path), "none")), [])
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
