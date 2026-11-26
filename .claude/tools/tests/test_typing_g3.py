"""Return annotations, group 3: the first 24 public defs of pipeline/plan_files.py (file order) declare a return type."""

import ast
import os
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_FILES = os.path.join(TOOLS, "pipeline", "plan_files.py")

GROUP = [
    "section_body",
    "checklist_items",
    "acceptance_items",
    "stage_of",
    "feature_dir",
    "stage_dir",
    "csv",
    "cell",
    "overlap",
    "plan_path",
    "init_plan",
    "read_phases",
    "parse_phase_rows",
    "is_mid_phase",
    "is_last_phase",
    "is_finish_dir",
    "is_finish_phase",
    "is_later_phase",
    "earlier_phase_tasks_mds",
    "next_phase_no",
    "ensure_finish",
    "insert_phase",
    "insert_before",
    "write_phases",
]


def _defs():
    with open(PLAN_FILES, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), PLAN_FILES)
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


class TypingGroup3Test(unittest.TestCase):
    def test_group_is_the_first_24_public_defs(self):
        with open(PLAN_FILES, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), PLAN_FILES)
        public = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        self.assertEqual(public[:24], GROUP)

    def test_every_group_def_has_return_annotation(self):
        defs = _defs()
        missing = [name for name in GROUP if defs[name].returns is None]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
