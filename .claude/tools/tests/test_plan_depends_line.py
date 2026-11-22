"""The `Depends:` line of task-N.md: written by `task --depends` and `task --before`, kept equal to the TASKS.md bullet depends, and not read as scope text."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_files, plan_rules
from pipeline.tasks import parse_tasks
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

FLAGS = "no-merge no-test no-audit no-retro no-index no-push no-clean"


class DependsLine(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True)  # plan check wants Predicted paths inside a git repo
        self.w = Path(self.d, ".work", "s")
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")
        self.p("stage-init", "--stage", "s", "--goal", "g", "--flags", FLAGS)

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        return rc, out.getvalue(), err.getvalue()

    def task(self, n, paths, **kw):
        extra = [x for k, v in kw.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        return self.p("task", "--stage", "s", "--id", str(n), "--title", f"Do {n}", "--paths", paths, "--test", "t", "--done", "ok", *extra)

    def before(self, n, paths, **kw):
        extra = [x for k, v in kw.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        return self.p("task", "--stage", "s", "--before", str(n), "--title", "New", "--paths", paths, "--test", "t", "--done", "ok", *extra)

    def file_deps(self, tid):
        """-> the ids on the `Depends:` line of task-N.md, [] when there is no such line."""
        for ln in (self.w / f"{tid}.md").read_text(encoding="utf-8").splitlines():
            if ln.startswith("Depends: "):
                return ln[len("Depends: ") :].split(", ")
        return []

    def parsed(self):
        return {t["id"]: t.get("depends", []) for t in parse_tasks(str(self.w / "TASKS.md"))}

    def test_task_with_depends_writes_line_after_wave(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends="1")
        lines = (self.w / "task-2.md").read_text(encoding="utf-8").splitlines()
        wave = next(i for i, ln in enumerate(lines) if ln.startswith("Wave: W"))
        self.assertEqual(lines[wave + 1], "Depends: task-1")
        self.assertEqual(self.file_deps("task-2"), ["task-1"])

    def test_multiple_depends_are_comma_and_space_separated(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js")
        self.task(3, "c/z.js", depends="1,2")
        self.assertIn("Depends: task-1, task-2\n", (self.w / "task-3.md").read_text(encoding="utf-8"))

    def test_no_line_when_no_dependency(self):
        self.task(1, "a/x.js")
        self.assertNotIn("Depends:", (self.w / "task-1.md").read_text(encoding="utf-8"))

    def test_edit_task_depends_sets_the_line_equal_to_tasks_md(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js")
        self.task(3, "c/z.js")
        rc, _out, _err = self.p("edit-task", "--stage", "s", "--id", "3", "--depends", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(self.file_deps("task-3"), ["task-1"])
        self.assertEqual(self.parsed()["task-3"], ["task-1"])

    def test_before_with_renumbering_keeps_every_line_equal_to_tasks_md(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends="1")
        self.task(3, "c/z.js", depends="2")
        rc, _out, _err = self.before(2, "q/new.js")
        self.assertEqual(rc, 0)
        self.assertEqual(self.file_deps("task-4"), ["task-3"])  # old task-3, renumbered
        self.assertEqual(self.file_deps("task-3"), ["task-1", "task-2"])  # old task-2 gains the edge on the new task
        for tid, deps in self.parsed().items():
            self.assertEqual(self.file_deps(tid), deps, tid)

    def test_depends_line_is_not_read_as_scope_text(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js")
        self.task(3, "c/z.js")
        path = self.w / "task-2.md"
        # decoy: a Depends line naming task-3, which is in the same wave and not a real dependency in TASKS.md
        path.write_text(path.read_text(encoding="utf-8").replace("Wave: W1\n", "Wave: W1\nDepends: task-3\n", 1), encoding="utf-8")
        self.assertIn("Depends: task-3\n", path.read_text(encoding="utf-8"))
        ents = plan_files.read_entries(str(self.w))
        e2 = next(e for e in ents if e["id"] == "task-2")
        self.assertEqual(plan_rules.check_unlisted_prereq(str(self.w), e2, ents), [])

    def test_empty_deps_keep_an_addendum_depends_line(self):
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends="1")
        path = self.w / "task-2.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n## ADDENDUM\nDepends: task-9\n", encoding="utf-8")
        rc, _out, _err = self.p("edit-task", "--stage", "s", "--id", "2", "--depends", "")
        txt = path.read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertNotIn("Depends: task-1", txt)
        self.assertIn("## ADDENDUM\nDepends: task-9\n", txt)


if __name__ == "__main__":
    unittest.main()
