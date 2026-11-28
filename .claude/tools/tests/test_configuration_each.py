"""each_of validates every `each` entry: an object with a non-empty string glob and cmd; an optional parallel group stays a string."""

import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

each_of = importlib.import_module("pipeline.configuration.project").each_of
ConfigError = importlib.import_module("pipeline.configuration.errors").ConfigError


class EachOfValidationTest(unittest.TestCase):
    def test_entry_without_glob_raises_naming_index_and_field(self):
        with self.assertRaises(ConfigError) as ctx:
            each_of({"each": [{"cmd": "make"}]})
        self.assertIn("each[0]", str(ctx.exception))
        self.assertIn("glob", str(ctx.exception))

    def test_entry_with_blank_glob_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            each_of({"each": [{"glob": "  ", "cmd": "make"}]})
        self.assertIn("glob", str(ctx.exception))

    def test_entry_without_cmd_raises_naming_field(self):
        with self.assertRaises(ConfigError) as ctx:
            each_of({"each": [{"glob": "*/Makefile"}]})
        self.assertIn("each[0]", str(ctx.exception))
        self.assertIn("cmd", str(ctx.exception))

    def test_non_dict_entry_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            each_of({"each": ["make"]})
        self.assertIn("each[0]", str(ctx.exception))

    def test_second_bad_entry_reports_its_index(self):
        with self.assertRaises(ConfigError) as ctx:
            each_of({"each": [{"glob": "a", "cmd": "b"}, {"glob": "c"}]})
        self.assertIn("each[1]", str(ctx.exception))

    def test_valid_entry_unchanged(self):
        entry = {"glob": "*/Makefile", "cmd": "make -C {dir}", "extra": 1}
        self.assertEqual(each_of({"each": [entry]}), [entry])

    def test_parallel_key_still_works_and_is_stripped(self):
        out = each_of({"each": [{"glob": "a", "cmd": "b", "parallel": " grp "}]})
        self.assertEqual(out, [{"glob": "a", "cmd": "b", "parallel": "grp"}])

    def test_blank_parallel_key_is_dropped(self):
        out = each_of({"each": [{"glob": "a", "cmd": "b", "parallel": "  "}]})
        self.assertEqual(out, [{"glob": "a", "cmd": "b"}])

    def test_non_string_parallel_key_raises(self):
        with self.assertRaises(ConfigError):
            each_of({"each": [{"glob": "a", "cmd": "b", "parallel": 3}]})

    def test_no_each_key_gives_empty_list(self):
        self.assertEqual(each_of({}), [])
