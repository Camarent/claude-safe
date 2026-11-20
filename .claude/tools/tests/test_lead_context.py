import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import lead_context as lc


def turn(i, c, r):
    return json.dumps({"message": {"usage": {"input_tokens": i, "cache_creation_input_tokens": c, "cache_read_input_tokens": r}}})


class T(unittest.TestCase):
    def test_warning(self):
        self.assertIsNone(lc.warning(150000))
        self.assertIsNone(lc.warning(None))
        self.assertEqual(lc.warning(209500), "lead context high: 209k tokens, hand off and start the next phase fresh")

    def test_source(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(lc.last_context_tokens(d))
            (Path(d) / "a.jsonl").write_text(turn(1, 2, 3) + "\nbad {\n" + turn(2, 1000, 200000) + "\n{}\n")
            self.assertEqual(lc.last_context_tokens(d), 201002)
            out = io.StringIO()
            lc.check(d, out)
            self.assertIn("201k", out.getvalue())

    def test_tail_read_uses_last_usage(self):
        filler = json.dumps({"message": {"content": "x" * 120}})
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lc, "TAIL", 300):
            body = (
                '"cut usage fragment'
                "\n"  # first line of the tail: cut, skipped
                + "\n".join([filler] * 2)
                + "\n"
                + turn(1, 2, 3)
                + "\n"
                + filler
                + "\n"
                + turn(10, 20, 30)
                + "\n"
                + filler
                + "\n"
            )
            (Path(d) / "a.jsonl").write_text(body)
            self.assertEqual(lc.last_context_tokens(d), 60)

    def test_tail_without_usage_is_none(self):
        filler = json.dumps({"message": {"content": "y" * 120}})
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lc, "TAIL", 200):
            body = turn(1, 2, 3) + "\n" + "\n".join([filler] * 5) + "\n"
            self.assertGreater(len(body), 200)
            (Path(d) / "a.jsonl").write_text(body)
            self.assertIsNone(lc.last_context_tokens(d))

    def test_silent(self):
        out = io.StringIO()
        lc.check("/nonexistent/x", out)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
