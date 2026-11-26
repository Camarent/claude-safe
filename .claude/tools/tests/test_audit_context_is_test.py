"""audit_context.is_test: test files by basename or dir, not by substring (synthetic, no I/O)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_context as C

TEST_PATHS = [
    "tests/test_scope.py",
    "pkg/test_util.py",
    "pkg/util_test.py",
    "web/app.test.js",
    "web/app.spec.ts",
    "src/__tests__/app.js",
    "tests/helpers.py",
]
NOT_TEST_PATHS = [
    "audit/latest.py",
    "lib/inspector.py",
    "src/contest.rs",
    "web/spectrum.js",
    "src/a.py",
]


class TestIsTest(unittest.TestCase):
    def test_test_paths_are_tests(self):
        for p in TEST_PATHS:
            with self.subTest(path=p):
                self.assertTrue(C.is_test(p))

    def test_substring_paths_are_not_tests(self):
        for p in NOT_TEST_PATHS:
            with self.subTest(path=p):
                self.assertFalse(C.is_test(p))


if __name__ == "__main__":
    unittest.main()
