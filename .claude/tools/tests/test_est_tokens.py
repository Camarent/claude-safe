"""est_tokens: conservative (ASCII / 3.5, every other char one token)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.util import est_tokens


class EstTokensTest(unittest.TestCase):
    def test_ascii_is_a_bit_under_one_token_per_three_chars(self):
        self.assertEqual(est_tokens("a" * 350), 100)
        self.assertEqual(est_tokens(""), 0)

    def test_japanese_and_cyrillic_count_one_token_per_char(self):
        self.assertEqual(est_tokens("日本語のテキスト"), 8)
        self.assertEqual(est_tokens("привет"), 6)
        self.assertGreater(est_tokens("привет мир"), est_tokens("hello wrld"))

    def test_mixed_text_adds_both(self):
        self.assertEqual(est_tokens("a" * 35 + "日" * 5), 15)


if __name__ == "__main__":
    unittest.main()
