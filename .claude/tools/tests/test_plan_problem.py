"""plan_problem: a Problem carries its kind and level; errors_of reads the level; every kind the plan modules emit has a level."""

import ast
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_gatemode
from pipeline.configuration import GATE_MODE_PREFIX
from pipeline.plan_problem import ERROR, KIND_LEVELS, WARN, Problem

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline")
PLANNING = os.path.join(PIPELINE, "configuration", "planning.py")
WARN_CONST_RE = re.compile(r'(?m)^([A-Z_]+_WARN_PREFIX)\s*=\s*"([a-z][a-z-]*):"')
KIND_IN_TEXT_RE = re.compile(r"([a-z][a-z-]*):(?=\s|$)")
# kinds of plan_*.py strings that are no check problem: a log line, a raised message, a header, an input value
NOT_PROBLEMS = frozenset(("cycle", "model", "note", "plan", "pr-title", "tool", "warn", "warning"))


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


class GateModeTest(unittest.TestCase):
    """plan check `gatemode:`: a final gate that runs a script whose header lists modes must name one of them."""

    HEADER = "# usage: scripts/gate.sh [--unit | --merge]\n"

    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.ws, True)
        self.addCleanup(shutil.rmtree, self.repo, True)

    def gate(self, cmd):
        """Write the project gate CMD into the workspace pipeline.json."""
        with open(os.path.join(self.ws, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"gates": [{"name": "gate", "cmd": cmd}]}, f)

    def script(self, text, name="gate.sh"):
        os.makedirs(os.path.join(self.repo, "scripts"), exist_ok=True)
        with open(os.path.join(self.repo, "scripts", name), "w", encoding="utf-8") as f:
            f.write(text)

    def problems(self):
        return plan_gatemode.check_gate_modes(self.ws, self.repo)

    def test_a_script_with_modes_run_without_a_flag_is_one_problem(self):
        self.gate("scripts/gate.sh")
        self.script(self.HEADER + "echo full\n")
        got = self.problems()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(GATE_MODE_PREFIX))
        self.assertIn("scripts/gate.sh", got[0])
        self.assertIn("--merge, --unit", got[0])

    def test_a_script_with_modes_run_with_a_flag_is_fine(self):
        self.gate("scripts/gate.sh --unit")
        self.script(self.HEADER + "echo unit\n")
        self.assertEqual(self.problems(), [])

    def test_a_script_without_a_mode_header_is_fine(self):
        self.gate("scripts/gate.sh")
        self.script("# plain gate\necho full\n")
        self.assertEqual(self.problems(), [])

    def test_a_missing_script_is_fine(self):
        self.gate("scripts/missing.sh")
        self.assertEqual(self.problems(), [])

    def test_a_mode_named_only_below_the_header_limit_is_fine(self):
        self.gate("scripts/gate.sh")
        self.script("\n" * 10 + "# --unit\necho full\n")
        self.assertEqual(self.problems(), [])

    def test_a_subcommand_without_modes_of_its_own_is_fine(self):
        self.gate("scripts/gate.sh check")
        self.script("# gate.sh sync | check | gate [--ext|--pytest]\necho x\n")
        self.assertEqual(self.problems(), [])

    def test_a_subcommand_with_modes_run_without_a_flag_is_one_problem(self):
        self.gate("scripts/gate.sh gate")
        self.script("# gate.sh sync | check | gate [--ext|--pytest]\necho x\n")
        self.assertEqual(len(self.problems()), 1)


if __name__ == "__main__":
    unittest.main()
