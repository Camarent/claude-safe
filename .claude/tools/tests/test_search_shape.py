"""Characterization: what the pack builder gets from code search on a fixture repo: the file set per query and the chunk-dict shape."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code


def search(root, q, k=3):
    return ask_code.query(root, q, k)


class Shape(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        os.makedirs(f"{self.d}/web")
        os.makedirs(f"{self.d}/node_modules/x")
        Path(self.d, "web/cart.js").write_text("function applyDiscount(cents, pct) {\n  return cents * (100 - pct) / 100;\n}\n", encoding="utf-8")
        Path(self.d, "web/user.js").write_text("function loginUser(name) {\n  return session.create(name);\n}\n", encoding="utf-8")
        Path(self.d, "node_modules/x/skip.js").write_text("function applyDiscount() {}\n", encoding="utf-8")

    def test_file_set_and_shape(self):
        hits = search(self.d, "discount applied to a price")
        self.assertEqual([h["file"] for h in hits], ["web/cart.js"])
        h = hits[0]
        self.assertEqual((h["start"], h["end"]), (1, 3))
        self.assertEqual(h["text"], Path(self.d, "web/cart.js").read_text(encoding="utf-8").rstrip("\n"))

    def test_other_query_other_file_and_no_match(self):
        self.assertEqual([h["file"] for h in search(self.d, "login the user")], ["web/user.js"])
        self.assertEqual(search(self.d, "zzzunrelated"), [])

    def test_k_limits_distinct_files(self):
        self.assertEqual(sorted(h["file"] for h in search(self.d, "function name cents session", 2)), ["web/cart.js", "web/user.js"])


if __name__ == "__main__":
    unittest.main()
