"""retro/feature_retro.py lsp_health_line: the retro line for failed `nav` calls.

Source: the lp usage log written by search/local.py usage_log (one JSON line per lp call, `cmd` "nav def" etc., `rc`).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from retro import feature_retro


class LspHealthLineTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.path = os.path.join(t.name, "lp-usage.jsonl")

    def write(self, *recs):
        with open(self.path, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def test_failing_nav_records_give_line(self):
        self.write(
            {"cmd": "nav def", "rc": 0},
            {"cmd": "nav refs", "rc": 1},
            {"cmd": "nav hover", "rc": 2},
            {"cmd": "find", "rc": 1},
        )
        self.assertEqual(feature_retro.lsp_health_line(self.path), ["language server: 2 nav calls failed or fell back"])

    def test_healthy_records_give_no_line(self):
        self.write({"cmd": "nav def", "rc": 0}, {"cmd": "find", "rc": 1, "idk": True})
        self.assertEqual(feature_retro.lsp_health_line(self.path), [])

    def test_no_log_gives_no_line(self):
        self.assertEqual(feature_retro.lsp_health_line(self.path), [])


if __name__ == "__main__":
    unittest.main()
