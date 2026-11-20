import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import context_eval as ce


class Pure(unittest.TestCase):
    def test_recall_counts_reference_files_found(self):
        self.assertEqual(ce.recall(["a", "b", "c"], ["a", "c", "x"]), (2, 3))
        self.assertEqual(ce.recall([], ["a"]), (0, 0))

    def test_overlap_is_jaccard_and_one_for_two_empty(self):
        self.assertEqual(ce.overlap(["a", "b"], ["b", "c"]), 1 / 3)
        self.assertEqual(ce.overlap([], []), 1.0)

    def test_spread(self):
        self.assertEqual(ce.spread([20, 22, 21]), (20, 22))
        self.assertEqual(ce.spread([]), (0, 0))

    def test_cold_runs_and_cache_check_use_the_naming_function(self):
        calls = []

        def fake(root, text, cfg, use_cache=True, per=None):
            calls.append(use_cache)
            if per is not None:
                per.append(("line one two three four five", ["a"], use_cache))
            return ["a", "b"]

        saved = ce.pc.model_files
        ce.pc.model_files = fake
        try:
            runs = ce.cold_runs("/r", "t", {}, ["a", "z"], 2)
            rows = ce.cache_check("/r", "t", {})
        finally:
            ce.pc.model_files = saved
        self.assertEqual([r["hits"] for r in runs], [1, 1])
        self.assertEqual(calls, [False, False, True, True, False, False])
        self.assertEqual(rows, [("line one two three four five", ["a"], ["a"], ["a"])])


if __name__ == "__main__":
    unittest.main()
