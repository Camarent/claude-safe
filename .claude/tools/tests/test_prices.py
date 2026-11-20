import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import prices


class PricesTest(unittest.TestCase):
    def test_lookup_order(self):
        self.assertEqual(prices.lookup("claude-opus-5-5")["input"], 4.0)
        self.assertEqual(prices.lookup("claude-opus-4-8")["input"], 5.0)
        self.assertEqual(prices.lookup("claude-sonnet-5-5")["output"], 10.0)
        self.assertEqual(prices.lookup("claude-sonnet-4-6")["input"], 3.0)
        self.assertEqual(prices.lookup("claude-haiku-5-5")["input"], 0.10)
        self.assertEqual(prices.lookup("claude-haiku-4-5")["input"], 1.0)

    def test_unknown(self):
        self.assertIsNone(prices.lookup("gpt-x"))
        self.assertIsNone(prices.lookup(None))
        self.assertIsNone(prices.cost("gpt-x", 5))

    def test_cost(self):
        c = prices.cost("claude-opus-5-5", input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000)
        self.assertAlmostEqual(c, 4 + 20 + 0.2 + 5)

    def test_date_set(self):
        self.assertRegex(prices.PRICES_DATE, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
