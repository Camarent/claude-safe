"""pipeline.empty_tests.find: a test function with no assert, raises check or mock assertion is reported; helpers, nested asserts, marked tests and syntax errors are not."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import empty_tests

PATH = "tests/test_x.py"


def names(source):
    return [name for name, _ in empty_tests.find(source, PATH)]


class TestFind(unittest.TestCase):
    def test_test_with_only_a_call_is_reported(self):
        src = "def test_a():\n    print('hi')\n"
        self.assertEqual(empty_tests.find(src, PATH), [("test_a", 1)])

    def test_method_in_class_with_only_a_call_is_reported(self):
        src = "class TestX:\n    def test_b(self):\n        run()\n"
        self.assertEqual(empty_tests.find(src, PATH), [("test_b", 2)])

    def test_each_assertion_form_passes(self):
        forms = [
            "    assert x == 1\n",
            "    self.assertEqual(x, 1)\n",
            "    pytest.raises(ValueError, f)\n",
            "    self.assertRaises(ValueError, f)\n",
            "    mock_fn.assert_called_once()\n",
            "    assert_ok(x)\n",
            "    with pytest.raises(ValueError):\n        f()\n",
        ]
        for body in forms:
            with self.subTest(body=body):
                self.assertEqual(names(f"def test_a():\n{body}"), [])

    def test_async_test_with_no_assertion_is_reported(self):
        src = "async def test_a():\n    await run()\n"
        self.assertEqual(names(src), ["test_a"])

    def test_non_test_helper_with_no_assert_is_not_reported(self):
        self.assertEqual(names("def helper():\n    run()\n"), [])

    def test_assert_in_nested_def_counts(self):
        src = "def test_a():\n    def inner():\n        assert True\n    inner()\n"
        self.assertEqual(names(src), [])

    def test_marked_test_is_skipped_on_def_line_or_line_above(self):
        above = "# noassert: smoke only, the import is the check\ndef test_a():\n    run()\n"
        same = "def test_b():  # noassert: smoke only\n    run()\n"
        self.assertEqual(names(above), [])
        self.assertEqual(names(same), [])

    def test_bare_marker_line_skips_the_test_below(self):
        self.assertEqual(names("# noassert:\ndef test_a():\n    run()\n"), [])

    def test_unmarked_neighbour_of_marked_test_is_reported(self):
        src = "# noassert: smoke\ndef test_a():\n    run()\n\ndef test_b():\n    run()\n"
        self.assertEqual(names(src), ["test_b"])

    def test_syntax_error_returns_empty(self):
        self.assertEqual(empty_tests.find("def test_a(:\n", PATH), [])

    def test_custom_marker_is_honoured(self):
        src = "# skip-check: smoke\ndef test_a():\n    run()\n"
        self.assertEqual(empty_tests.find(src, PATH, marker="# skip-check:"), [])
        self.assertEqual(empty_tests.find(src, PATH), [("test_a", 2)])


if __name__ == "__main__":
    unittest.main()
