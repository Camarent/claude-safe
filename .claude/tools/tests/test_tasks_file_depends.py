"""`Depends:` lines in a task file (task-N.md next to TASKS.md) join the TASKS.md ids of that task."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.tasks import depends_problem, parse_tasks
from tests.support.tmpdirs import tracked_mkdtemp

HEAD = "| task | what | owner | price | review |\n|---|---|---|---|---|\n"


def stage(tasks_text, task_files=None):
    """A stage dir with TASKS.md and optional task-N.md files -> path of TASKS.md."""
    d = Path(tracked_mkdtemp())
    (d / "TASKS.md").write_text(tasks_text, encoding="utf-8")
    for name, text in (task_files or {}).items():
        (d / name).write_text(text, encoding="utf-8")
    return str(d / "TASKS.md")


class TaskFileDepends(unittest.TestCase):
    def test_depends_in_task_file_is_read(self):
        md = stage(
            HEAD + "| task-3 | a | dev | 1 | wave W1, haiku |\n| task-4 | b | dev | 1 | wave W2, haiku |\n\n## Graph\nwave 1: task-3\nwave 2: task-4\n",
            {"task-4.md": "# task-4\n\nDepends: task-3\n"},
        )
        t = parse_tasks(md)
        by = {x["id"]: x.get("depends", []) for x in t}
        self.assertEqual(by["task-4"], ["task-3"])
        self.assertIsNone(depends_problem(t))

    def test_task_file_depends_must_be_before_the_wave(self):
        md = stage(
            HEAD + "| task-3 | a | dev | 1 | wave W1, haiku |\n| task-4 | b | dev | 1 | wave W1, haiku |\n\n## Graph\nwave 1: task-3, task-4\n",
            {"task-4.md": "Depends: task-3\n"},
        )
        t = parse_tasks(md)
        self.assertEqual({x["id"]: x.get("depends", []) for x in t}["task-4"], ["task-3"])
        self.assertIn("later wave", depends_problem(t) or "")

    def test_same_ids_on_both_sides_give_no_duplicates(self):
        md = stage(
            HEAD
            + "| task-1 | a | dev | 1 | wave W1, haiku |\n| task-2 | b Depends: task-1 | dev | 1 | wave W2, haiku |\n\n## Graph\nwave 1: task-1\nwave 2: task-2\n",
            {"task-2.md": "Depends: task-1\n"},
        )
        t = parse_tasks(md)
        self.assertEqual({x["id"]: x.get("depends", []) for x in t}["task-2"], ["task-1"])

    def test_missing_task_file_is_ignored(self):
        md = stage(HEAD + "| task-1 | a | dev | 1 | wave W1, haiku |\n\n## Graph\nwave 1: task-1\n")
        t = parse_tasks(md)
        self.assertEqual([x["id"] for x in t], ["task-1"])
        self.assertNotIn("depends", t[0])


if __name__ == "__main__":
    unittest.main()
