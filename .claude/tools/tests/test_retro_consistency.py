import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import lp_share
from retro import retro_numbers as rn
from tests.support.tmpdirs import tracked_mkdtemp


class DigestCheckTest(unittest.TestCase):
    def _files(self, header):
        d = tracked_mkdtemp()
        m, dg = os.path.join(d, "metrics.jsonl"), os.path.join(d, "digest.md")
        with open(m, "w") as f:
            for c in (0.5, 0.25):
                f.write(json.dumps({"ev": "session", "cost_usd": c, "stage": "S"}) + "\n")
            f.write(json.dumps({"ev": "total", "cost_usd": 9}) + "\n")
        with open(dg, "w") as f:
            f.write(header + "\n")
        return m, dg

    def test_equal_is_quiet(self):
        m, dg = self._files("cost per agent and model (2 sessions, $0.75):")
        self.assertEqual(rn.digest_check(m, dg), [])

    def test_stale_digest_warns(self):
        m, dg = self._files("cost per agent and model (1 sessions, $0.50):")
        self.assertEqual(rn.digest_check(m, dg)[0][0], "digest-stale")

    def test_missing_file_is_quiet(self):
        self.assertEqual(rn.digest_check("/nonexistent/m", "/nonexistent/d"), [])

    def test_lead_rows_need_window(self):
        m, _ = self._files("x")
        self.assertEqual(rn.lead_rows(m, "", []), [])


class SearchNamesTest(unittest.TestCase):
    def test_both_names_counted(self):
        for cmd in ("lp find x", "python3 /a/local.py find x", "local.py nav def a f"):
            self.assertTrue(lp_share._bash_kind(cmd).startswith("local:"), cmd)
        self.assertRegex("python3 local.py nav", rn.LP_RE)


if __name__ == "__main__":
    unittest.main()
