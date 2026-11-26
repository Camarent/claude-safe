"""`cs-plan batch --stage S --file tasks.json`: same result as one `cs-plan task` per entry; a bad entry adds none."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

FLAGS = "no-merge no-test no-audit no-retro no-index no-push no-clean"
TASKS = [
    {"id": 1, "title": "Do one", "paths": "a/x.js", "test": "t", "done": "ok"},
    {"id": 2, "title": "Do two", "paths": "b/y.js", "test": "t", "done": "ok", "depends": "1"},
    {"id": 3, "title": "Do three", "paths": "c/z.js", "test": "t", "done": "ok", "turns": 30},
]


class Batch(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)

    def repo(self):
        d = tracked_mkdtemp()
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)  # plan check wants Predicted paths inside a git repo
        Path(d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")
        return d

    def p(self, d, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", d])
        return rc, out.getvalue(), err.getvalue()

    def init(self, d):
        self.p(d, "stage-init", "--stage", "s", "--goal", "g", "--flags", FLAGS)

    def tasks_md(self, d):
        return Path(d, ".work", "s", "TASKS.md").read_text(encoding="utf-8")

    def batch_file(self, d, data):
        path = Path(d, "tasks.json")
        path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
        return str(path)

    def test_batch_matches_one_task_call_each(self):
        one, many = self.repo(), self.repo()
        self.init(one)
        self.init(many)
        for t in TASKS:
            argv = ["task", "--stage", "s", "--id", str(t["id"]), "--title", t["title"], "--paths", t["paths"], "--test", t["test"], "--done", t["done"]]
            if "depends" in t:
                argv += ["--depends", t["depends"]]
            if "turns" in t:
                argv += ["--turns", str(t["turns"])]
            self.assertEqual(self.p(one, *argv)[0], 0)
        rc, out, err = self.p(many, "batch", "--stage", "s", "--file", self.batch_file(many, TASKS))
        self.assertEqual(rc, 0, err)
        self.assertIn("OK 3 tasks", out)
        self.assertEqual(self.tasks_md(many), self.tasks_md(one))
        self.assertEqual(
            sorted(os.listdir(Path(many, ".work", "s"))),
            sorted(os.listdir(Path(one, ".work", "s"))),
        )

    def test_invalid_entry_adds_none_and_names_it(self):
        d = self.repo()
        self.init(d)
        before = self.tasks_md(d)
        bad = [TASKS[0], {"id": 2, "paths": "b/y.js", "test": "t", "done": "ok"}, TASKS[2]]  # entry 2 has no title
        rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", self.batch_file(d, bad))
        self.assertEqual(rc, 2)
        self.assertIn("task #2", err)
        self.assertEqual(self.tasks_md(d), before)
        self.assertFalse(Path(d, ".work", "s", "task-1.md").exists())

    def test_stage_or_dir_key_is_rejected(self):
        d = self.repo()
        self.init(d)
        for key in ("stage", "dir"):
            bad = [TASKS[0], {**TASKS[1], key: "other"}]
            rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", self.batch_file(d, bad))
            self.assertEqual(rc, 2)
            self.assertIn("task #2", err)
            self.assertFalse(Path(d, ".work", "s", "task-1.md").exists())
            self.assertFalse(Path(d, ".work", "other").exists())

    def test_non_numeric_id_adds_none(self):
        d = self.repo()
        self.init(d)
        before = self.tasks_md(d)
        bad = [TASKS[0], {**TASKS[1], "id": "abc"}]
        rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", self.batch_file(d, bad))
        self.assertEqual(rc, 2)
        self.assertIn("task #2 has invalid id", err)
        self.assertEqual(self.tasks_md(d), before)
        self.assertFalse(Path(d, ".work", "s", "task-1.md").exists())

    def test_unexpected_error_midway_rolls_back(self):
        d = self.repo()
        self.init(d)
        before = self.tasks_md(d)
        real = cli_plan.cmd_task
        calls = []

        def flaky(b):
            calls.append(b.id)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(b)

        cli_plan.cmd_task = flaky
        self.addCleanup(setattr, cli_plan, "cmd_task", real)
        with self.assertRaises(OSError):
            self.p(d, "batch", "--stage", "s", "--file", self.batch_file(d, TASKS))
        self.assertEqual(self.tasks_md(d), before)
        self.assertFalse(Path(d, ".work", "s", "task-1.md").exists())

    def test_directory_as_file_is_a_plan_error(self):
        d = self.repo()
        self.init(d)
        rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", d)
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err)

    def test_missing_file_is_a_plan_error(self):
        d = self.repo()
        self.init(d)
        rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", str(Path(d, "nope.json")))
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err)

    def test_failure_midway_rolls_back_earlier_tasks(self):
        d = self.repo()
        self.init(d)
        before = self.tasks_md(d)
        bad = [TASKS[0], {"id": 2, "title": "x", "paths": "b/y.js", "test": "t", "done": "ok", "depends": "9"}]  # task 9 unknown
        rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", self.batch_file(d, bad))
        self.assertEqual(rc, 2)
        self.assertIn("task #2 failed, none added", err)
        self.assertEqual(self.tasks_md(d), before)
        self.assertFalse(Path(d, ".work", "s", "task-1.md").exists())

    def test_non_list_json_is_rejected(self):
        d = self.repo()
        self.init(d)
        rc, _out, err = self.p(d, "batch", "--stage", "s", "--file", self.batch_file(d, {"id": 1}))
        self.assertEqual(rc, 2)
        self.assertIn("JSON list", err)
