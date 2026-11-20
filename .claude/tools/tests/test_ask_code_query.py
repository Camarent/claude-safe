"""ask_code.query (the search the pack builder uses) and strata.strata_call, migrated from the old localsearch tests."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import ask_code, strata


class Query(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        os.makedirs(f"{self.d}/web/ui")
        os.makedirs(f"{self.d}/node_modules/x")
        Path(f"{self.d}/web/ui/cart.js").write_text("function applyDiscount(cents, pct) {\n  return cents * (100 - pct) / 100;\n}\n", encoding="utf-8")
        Path(f"{self.d}/web/ui/user.js").write_text("function loginUser(name) {\n  return session.create(name);\n}\n", encoding="utf-8")
        Path(f"{self.d}/node_modules/x/skip.js").write_text("function applyDiscount() {}\n", encoding="utf-8")

    def paths(self):
        con = ask_code.connect(self.d)
        try:
            return sorted(con.execute("select distinct path from spans").fetchall())
        finally:
            con.close()

    def test_index_skips_junk_and_is_incremental(self):
        self.assertEqual(ask_code.refresh(self.d, embed=0), (2, 0))
        self.assertEqual(self.paths(), [("web/ui/cart.js",), ("web/ui/user.js",)])
        with open(f"{self.d}/web/ui/user.js", "a", encoding="utf-8") as f:
            f.write("// more\n")
        self.assertEqual(ask_code.refresh(self.d, embed=0), (1, 0))

    def test_query_finds_by_identifier_words(self):
        hit = ask_code.query(self.d, "discount percentage applied to a price")[0]
        self.assertEqual(hit["file"], "web/ui/cart.js")
        self.assertEqual(hit["path"], hit["file"])
        self.assertEqual((hit["start"], hit["end"]), (1, 3))
        self.assertIn("100 - pct", hit["text"])

    def test_query_no_match_and_k(self):
        self.assertEqual(ask_code.query(self.d, "zzzunrelated"), [])
        self.assertEqual(len(ask_code.query(self.d, "function name cents session", 1)), 1)

    def test_broken_symlink_is_skipped_by_index(self):  # a dangling x.py must not crash the indexer
        Path(self.d, "ok.py").write_text("def f():\n    return 1\n")
        os.symlink(os.path.join(self.d, "gone"), os.path.join(self.d, "x.py"))
        ask_code.refresh(self.d, embed=0)
        self.assertIn(("ok.py",), self.paths())
        self.assertNotIn(("x.py",), self.paths())


class StrataKey(unittest.TestCase):
    def test_strata_call_uses_strata_key_lookup(self):  # no STRATA_KEY / ANTHROPIC_AUTH_TOKEN: the /data/state/api-key fallback is used
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            return io.BytesIO(b'{"content": [{"type": "text", "text": "hi"}]}')

        env = {k: v for k, v in os.environ.items() if k not in ("STRATA_KEY", "ANTHROPIC_AUTH_TOKEN")}
        key = mock.patch.object(strata, "strata_key", return_value="file-key")
        url = mock.patch.object(strata.urllib.request, "urlopen", side_effect=fake_urlopen)
        with mock.patch.dict(os.environ, env, clear=True), key, url:
            self.assertEqual(strata.strata_call("hi"), "hi")
        self.assertEqual(seen["auth"], "Bearer file-key")


if __name__ == "__main__":
    unittest.main()
