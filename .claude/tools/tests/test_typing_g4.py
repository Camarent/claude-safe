"""Return annotations, group 4: the remaining public defs of pipeline/plan_files.py (after the first 24 of group 3) declare a return type; none is left unannotated."""

import ast
import os
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_FILES = os.path.join(TOOLS, "pipeline", "plan_files.py")

GROUP = [
    "phase_ref",
    "phases_ordered",
    "next_open_phase",
    "started_tasks",
    "tasks_path",
    "read_entries",
    "bullet",
    "graph_line",
    "write_tasks",
    "init_stage",
    "path_modes",
    "paths_in",
    "is_line_based",
    "wave_conflict",
    "task_affected_tests",
    "task_type",
    "task_inputs",
    "task_paths",
    "task_path_modes",
    "with_qrun",
    "set_task_field",
    "depends_line",
    "set_depends_line",
    "render_task",
]


def _tree():
    with open(PLAN_FILES, encoding="utf-8") as fh:
        return ast.parse(fh.read(), PLAN_FILES)


def _public_defs(tree):
    return [n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]


class TypingGroup4Test(unittest.TestCase):
    def test_group_is_the_public_defs_after_the_first_24(self):
        public = [n.name for n in _public_defs(_tree())]
        self.assertEqual(public[24:], GROUP)

    def test_every_group_def_has_return_annotation(self):
        defs = {n.name: n for n in _public_defs(_tree())}
        missing = [name for name in GROUP if defs[name].returns is None]
        self.assertEqual(missing, [])

    def test_no_public_def_lacks_return_annotation(self):
        missing = [n.name for n in _public_defs(_tree()) if n.returns is None]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
