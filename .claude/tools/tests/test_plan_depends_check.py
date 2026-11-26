"""plan_depends: the `Depends:` line of a task file must name the TASKS.md depends of its entry."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.plan_depends import depends_line_problems
from tests.support.tmpdirs import tracked_mkdtemp


def stage(task_files):
    """A stage dir with task-N.md files (name -> text) -> dir path."""
    d = Path(tracked_mkdtemp())
    for name, text in task_files.items():
        (d / name).write_text(text, encoding="utf-8")
    return str(d)


def ent(tid, depends=()):
    return {"id": tid, "wave": 2, "depends": list(depends)}


class DependsLineProblems(unittest.TestCase):
    def test_missing_line_is_a_problem(self):
        d = stage({"task-4.md": "# task-4\n\nPredicted paths: a.py\n"})
        got = depends_line_problems(d, [ent("task-4", ["task-3"])])
        self.assertEqual(got, ["task-4: TASKS.md depends on task-3 but task-4.md has no Depends line"])

    def test_extra_line_is_a_problem(self):
        d = stage({"task-4.md": "# task-4\n\nDepends: task-3\n"})
        got = depends_line_problems(d, [ent("task-4")])
        self.assertEqual(got, ["task-4: task-4.md depends on task-3 but TASKS.md has no depends"])

    def test_different_ids_are_a_problem_naming_both_sets(self):
        d = stage({"task-4.md": "Depends: task-2\n"})
        got = depends_line_problems(d, [ent("task-4", ["task-3"])])
        self.assertEqual(got, ["task-4: TASKS.md depends on task-3 but task-4.md depends on task-2"])

    def test_equal_sets_in_different_order_are_fine(self):
        d = stage({"task-5.md": "Depends: ops-2, task-3\n"})
        self.assertEqual(depends_line_problems(d, [ent("task-5", ["task-3", "ops-2"])]), [])

    def test_no_depends_on_either_side_is_fine(self):
        d = stage({"task-6.md": "# task-6\n\nPredicted paths: a.py\n"})
        self.assertEqual(depends_line_problems(d, [ent("task-6")]), [])

    def test_ops_ids_are_compared(self):
        d = stage({"task-7.md": "Depends: ops-1\n"})
        self.assertEqual(depends_line_problems(d, [ent("task-7", ["ops-1"])]), [])
        self.assertEqual(
            depends_line_problems(d, [ent("task-7", ["ops-2"])]),
            ["task-7: TASKS.md depends on ops-2 but task-7.md depends on ops-1"],
        )

    def test_line_must_start_the_line(self):
        # a sentence that mentions Depends mid-line is not a Depends line
        d = stage({"task-8.md": "Note: Depends: task-3 is discussed below\n"})
        self.assertEqual(depends_line_problems(d, [ent("task-8")]), [])

    def test_missing_task_file_is_left_to_the_task_file_check(self):
        d = stage({})
        self.assertEqual(depends_line_problems(d, [ent("task-9", ["task-3"])]), [])

    def test_wired_into_the_task_file_problems(self):
        # plan_check._task_file_problems reports the mismatch of its entry (the wiring, not only the module)
        d = stage({"task-4.md": "# task-4\n\nPredicted paths: a.py\n"})
        e = ent("task-4", ["task-3"])
        got = plan_check._task_file_problems(d, e, [e], d, [], None, [])
        self.assertIn("task-4: TASKS.md depends on task-3 but task-4.md has no Depends line", got)


if __name__ == "__main__":
    unittest.main()
