import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import local
from tests.support.tmpdirs import tracked_mkdtemp


class UsageLogTest(unittest.TestCase):
    def setUp(self):
        self.p = os.path.join(tracked_mkdtemp(), "u.jsonl")
        os.environ["LP_USAGE_LOG"] = self.p

    def tearDown(self):
        os.environ.pop("LP_USAGE_LOG", None)

    def test_one_valid_line_per_call_even_on_failure(self):
        local.usage_log(["find", "x" * 500], 1, 0.5, 0)
        local.usage_log(["nav", "def", "f"], 2, 0.1, 0)
        with open(self.p, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f]
        self.assertEqual([r["cmd"] for r in recs], ["find", "nav def"])
        self.assertTrue(recs[0]["idk"])
        self.assertEqual(len(recs[0]["q"]), 200)

    def test_log_write_never_raises(self):
        os.environ["LP_USAGE_LOG"] = "/nonexistent-dir/x/u.jsonl"
        self.assertIsNone(local.usage_log(["find", "q"], 0, 0.1, 1))
        self.assertFalse(os.path.exists("/nonexistent-dir"))


if __name__ == "__main__":
    unittest.main()
