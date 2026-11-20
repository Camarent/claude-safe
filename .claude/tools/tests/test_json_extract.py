"""The three JSON-extraction callers keep their behaviour behind util.first_json_obj."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_parsers
from localmodel import askmodel, localdev


class JsonExtractTest(unittest.TestCase):
    def test_jsonobj_greedy(self):
        self.assertEqual(askmodel.jsonobj('noise {"a": 1} tail'), {"a": 1})
        self.assertEqual(askmodel.jsonobj('{"a": {"b": 2}} x'), {"a": {"b": 2}})
        self.assertEqual(askmodel.jsonobj('{"a": 1} and {"b": 2}'), {})  # greedy span is not JSON
        self.assertEqual(askmodel.jsonobj("{bad"), {})
        self.assertEqual(askmodel.jsonobj("none"), {})
        self.assertEqual(askmodel.jsonobj('{"a":\n1}'), {"a": 1})  # DOTALL

    def test_parse_json_obj_first(self):
        self.assertEqual(localdev.parse_json_obj('{"a": 1} and {"b": 2}'), {"a": 1})
        self.assertEqual(localdev.parse_json_obj('{x} {"b": 2}'), {"b": 2})
        self.assertIsNone(localdev.parse_json_obj("no json { here"))
        self.assertIsNone(localdev.parse_json_obj("[1, 2]"))
        self.assertIsNone(localdev.parse_json_obj(None))

    def test_json_doc(self):
        self.assertEqual(audit_parsers.json_doc(" [1, 2] "), [1, 2])
        self.assertEqual(audit_parsers.json_doc("3"), 3)
        self.assertEqual(audit_parsers.json_doc('warn\n{"a": 1}\nmore'), {"a": 1})
        self.assertEqual(audit_parsers.json_doc("noise\n[1]\n"), [1])
        self.assertEqual(audit_parsers.json_doc('x {"a": 1}\n{"b": 2}'), {"b": 2})  # only line-start openers
        with self.assertRaises(ValueError):
            audit_parsers.json_doc("nothing here")


if __name__ == "__main__":
    unittest.main()
