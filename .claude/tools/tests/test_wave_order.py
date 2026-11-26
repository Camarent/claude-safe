import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.cli_wave import order_by_model


def _d(i, model):
    return {"id": f"a{i}", "model": model}


class OrderByModelTest(unittest.TestCase):
    def test_groups_same_model_together_in_first_seen_order(self):
        ds = [_d(1, "sonnet"), _d(2, "haiku"), _d(3, "sonnet"), _d(4, "opus"), _d(5, "haiku")]
        self.assertEqual([d["id"] for d in order_by_model(ds)], ["a1", "a3", "a2", "a5", "a4"])

    def test_stable_within_a_model(self):
        ds = [_d(1, "haiku"), _d(2, "haiku"), _d(3, "haiku")]
        self.assertEqual([d["id"] for d in order_by_model(ds)], ["a1", "a2", "a3"])

    def test_empty(self):
        self.assertEqual(order_by_model([]), [])

    def test_does_not_mutate_input(self):
        ds = [_d(1, "opus"), _d(2, "haiku")]
        order_by_model(ds)
        self.assertEqual([d["id"] for d in ds], ["a1", "a2"])

    def test_missing_model_is_its_own_group(self):
        ds = [{"id": "a1"}, _d(2, "haiku"), {"id": "a3"}]
        self.assertEqual([d["id"] for d in order_by_model(ds)], ["a1", "a3", "a2"])
