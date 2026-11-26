"""Task lines: the plain list item is what we write; old boxed lines still parse."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_files as pf
from pipeline import tasks

PLAIN = "- task-1 Build the a module - review: wave W1\n"
BOXED = "- [ ] task-1 Build the a module - review: wave W1\n"
GRAPH = "\n## Graph\nwave 1: ops-1 | wave 2: task-1, task-2\nflags: no-merge\n"


def _entry(**kw):
    e = {"id": "task-1", "title": "Build the a module", "owner": "", "wave": 1, "review": "", "depends": []}
    e.update(kw)
    return e


class TaskLineFormatTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tlf-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "TASKS.md")

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_writer_emits_plain_bullet_without_box(self):
        line = pf.bullet(_entry(depends=["task-0"]))
        self.assertEqual(line, "- task-1 Build the a module - review: wave W1 - depends: task-0")
        self.assertNotIn("[", line)

    def test_plain_and_boxed_bullets_match_reader_regex(self):
        for line in (PLAIN, BOXED):
            m = pf.BULLET.match(line.rstrip("\n"))
            self.assertIsNotNone(m, line)
            assert m is not None
            self.assertEqual(m[1], "task-1")
            self.assertEqual(m[2], "Build the a module")
            self.assertEqual(m[4], "1")

    def test_task_row_accepts_both_forms(self):
        for line in (PLAIN, BOXED, "- [x] task-1 Build\n", "* task-1 Build\n"):
            row = tasks._task_row(line)
            self.assertEqual(row["id"], "task-1", line)

    def test_manual_step_accepts_both_forms(self):
        for line in ("- ops-1 manual: rotate the key\n", "- [ ] ops-1 manual: rotate the key\n", "- [X] ops-1 manual: rotate the key\n"):
            row = tasks._task_row(line)
            self.assertEqual(row["id"], "ops-1", line)
            self.assertEqual(row["manual"], "rotate the key")

    def test_parse_tasks_plain_and_boxed_with_graph_order(self):
        for body in (PLAIN + "- task-2 Second - review: wave W2 - depends: task-1\n", BOXED + "- [ ] task-2 Second - review: wave W2 - depends: task-1\n"):
            self._write("## Tasks\n" + body + GRAPH.replace("wave 1: ops-1 | wave 2: task-1, task-2", "wave 1: task-1 | wave 2: task-2"))
            rows = tasks.parse_tasks(self.path)
            self.assertEqual([r["id"] for r in rows], ["task-1", "task-2"])

    def test_parse_tasks_plain_manual_step_in_graph(self):
        self._write("## Tasks\n" + PLAIN + "- ops-1 manual: apply the forms\n" + GRAPH)
        rows = tasks.parse_tasks(self.path)
        self.assertEqual([r["id"] for r in rows], ["ops-1", "task-1"])
        self.assertEqual(rows[0]["manual"], "apply the forms")

    def test_read_entries_reads_plain_lines_the_writer_wrote(self):
        entries = [_entry(id="task-1"), _entry(id="task-2", title="Second", depends=["task-1"], wave=2)]
        pf.write_tasks(self.dir, entries)
        text = open(self.path, encoding="utf-8").read()
        self.assertNotIn("[ ]", text)
        got = pf.read_entries(self.dir)
        self.assertEqual([e["id"] for e in got], ["task-1", "task-2"])
        self.assertEqual(got[1]["depends"], ["task-1"])


if __name__ == "__main__":
    unittest.main()
