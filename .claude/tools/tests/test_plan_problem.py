"""plan_problem: a Problem carries its kind and level; errors_of reads the level; every kind the plan modules emit has a level."""

import ast
import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.plan_problem import ERROR, KIND_LEVELS, WARN, Problem

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline")
PLANNING = os.path.join(PIPELINE, "configuration", "planning.py")
WARN_CONST_RE = re.compile(r'(?m)^([A-Z_]+_WARN_PREFIX)\s*=\s*"([a-z][a-z-]*):"')
KIND_IN_TEXT_RE = re.compile(r"([a-z][a-z-]*):(?=\s|$)")
# kinds of plan_*.py strings that are no check problem: a log line, a raised message, a header, an input value
NOT_PROBLEMS = frozenset(("cycle", "model", "note", "plan", "pr-title", "tool", "warn"))


def emitted_kinds(source):
    """-> the kinds of the string literals in SOURCE that start with `kind:` (comments are not strings, so they are not counted)."""
    kinds = set()
    for node in ast.walk(ast.parse(source)):
        head = node.values[0] if isinstance(node, ast.JoinedStr) and node.values else node
        text = head.value if isinstance(head, ast.Constant) and isinstance(head.value, str) else ""
        m = KIND_IN_TEXT_RE.match(text)
        if m:
            kinds.add(m[1])
    return kinds


class ProblemTest(unittest.TestCase):
    def test_problem_equals_its_text_and_carries_kind_and_level(self):
        p = Problem("size", WARN, "size: x")
        self.assertIsInstance(p, str)
        self.assertEqual(p, "size: x")
        self.assertEqual((p.kind, p.level, p.text), ("size", WARN, "size: x"))

    def test_parse_takes_kind_and_level_from_the_table(self):
        p = Problem.parse("ci-drift: repo differs")
        self.assertEqual((p.kind, p.level), ("ci-drift", ERROR))
        self.assertEqual(Problem.parse("neg: item is negative").level, WARN)

    def test_unknown_or_missing_kind_is_an_error(self):
        self.assertEqual(Problem.parse("zzz-new: x").level, ERROR)
        p = Problem.parse("no kind token here")
        self.assertEqual((p.kind, p.level), ("", ERROR))

    def test_levels_are_only_error_or_warn(self):
        self.assertTrue(set(KIND_LEVELS.values()) <= {ERROR, WARN})

    def test_errors_of_keeps_errors_in_order(self):
        probs = ["size: a", "ci-drift: b", "zzz-new: c", "neg: d"]
        self.assertEqual(plan_check.errors_of(probs), ["ci-drift: b", "zzz-new: c"])

    def test_testcmd_is_an_error_and_ignored_done_is_registered(self):
        self.assertEqual(KIND_LEVELS["testcmd"], ERROR)
        self.assertEqual(KIND_LEVELS["ignored-done"], ERROR)
        self.assertEqual(KIND_LEVELS["research-hint"], WARN)


class KindRegistryTest(unittest.TestCase):
    def test_every_warn_prefix_constant_in_planning_has_a_level(self):
        with open(PLANNING, encoding="utf-8") as f:
            found = WARN_CONST_RE.findall(f.read())
        self.assertGreater(len(found), 5)
        missing = [name for name, kind in found if kind not in KIND_LEVELS]
        self.assertEqual(missing, [])

    def test_every_kind_the_plan_modules_emit_has_a_level(self):
        emitted = set()
        for path in glob.glob(os.path.join(PIPELINE, "plan_*.py")):
            with open(path, encoding="utf-8") as f:
                emitted |= emitted_kinds(f.read())
        self.assertIn("ci-drift", emitted)
        self.assertEqual(sorted(emitted - NOT_PROBLEMS - set(KIND_LEVELS)), [])

    def test_decoy_kind_in_a_comment_is_not_counted(self):
        self.assertEqual(emitted_kinds('x = 1  # "bogus: text"\n'), set())
        self.assertEqual(emitted_kinds('msg = "bogus: text"\n'), {"bogus"})


if __name__ == "__main__":
    unittest.main()
