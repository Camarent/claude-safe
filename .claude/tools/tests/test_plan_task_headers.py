"""`cs-plan task --type/--outputs/--inputs` (and the batch keys of the same names) write the Type:, Outputs:, Inputs: headers of task-N.md; an absent flag writes no line, so the task reads as code."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_files
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

FLAGS = "no-merge no-test no-audit no-retro no-index no-push no-clean"
OUT = "docs/x.md"
ENTRY = {"id": "task-1", "title": "Research it", "wave": 1, "depends": []}


class RenderTaskHeaders(unittest.TestCase):
    def args(self, **kw):
        base = {"pattern": "", "affected": "", "test": "t", "done": "ok", "turns": 25}
        return SimpleNamespace(**{**base, **kw})

    def test_headers_written_when_given(self):
        text = plan_files.render_task(ENTRY, self.args(type="research", outputs=OUT, inputs="a/y.md"), [OUT])
        self.assertIn("Type: research\n", text)
        self.assertIn(f"Outputs: {OUT}\n", text)
        self.assertIn("Inputs: a/y.md\n", text)
        self.assertEqual(plan_files.task_type(text), "research")
        self.assertEqual(plan_files.task_outputs(text), [OUT])
        self.assertEqual(plan_files.task_inputs(text), ["a/y.md"])

    def test_no_header_lines_when_absent(self):
        text = plan_files.render_task(ENTRY, self.args(), ["a/x.js"])
        for head in ("Type:", "Outputs:", "Inputs:"):
            self.assertNotIn(head, text)
        self.assertEqual(plan_files.task_type(text), "code")


class TaskCommand(unittest.TestCase):
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
        self.assertEqual(self.p(d, "stage-init", "--stage", "s", "--goal", "g", "--flags", FLAGS)[0], 0)

    def task_text(self, d, tid):
        return Path(d, ".work", "s", f"{tid}.md").read_text(encoding="utf-8")

    def task_argv(self, tid, *extra, paths="b/y.js"):
        return ["task", "--stage", "s", "--id", str(tid), "--title", f"Do {tid}", "--paths", paths, "--test", "t", "--done", "ok", *extra]

    def test_flags_write_headers(self):
        d = self.repo()
        self.init(d)
        rc, _, err = self.p(d, *self.task_argv(1, "--type", "research", "--outputs", OUT, "--inputs", "a/y.md", paths=OUT))
        self.assertEqual(rc, 0, err)
        text = self.task_text(d, "task-1")
        self.assertIn("Type: research\n", text)
        self.assertIn(f"Outputs: {OUT}\n", text)
        self.assertIn("Inputs: a/y.md\n", text)

    def test_absent_flags_write_no_type_line(self):
        d = self.repo()
        self.init(d)
        self.assertEqual(self.p(d, *self.task_argv(1))[0], 0)
        text = self.task_text(d, "task-1")
        self.assertNotIn("Type:", text)
        self.assertNotIn("Outputs:", text)
        self.assertNotIn("Inputs:", text)

    def test_unknown_type_is_rejected(self):
        d = self.repo()
        self.init(d)
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            cli_plan.main([*self.task_argv(1, "--type", "bogus"), "--dir", d])
        self.assertFalse(Path(d, ".work", "s", "task-1.md").exists())

    def test_batch_keys_reach_task(self):
        d = self.repo()
        self.init(d)
        entry = {"id": 1, "title": "Research it", "paths": OUT, "test": "t", "done": "ok", "type": "research", "outputs": OUT, "inputs": ["a/y.md"]}
        path = Path(d, "tasks.json")
        path.write_text(json.dumps([entry]), encoding="utf-8")
        rc, _, err = self.p(d, "batch", "--stage", "s", "--file", str(path))
        self.assertEqual(rc, 0, err)
        text = self.task_text(d, "task-1")
        self.assertIn("Type: research\n", text)
        self.assertIn(f"Outputs: {OUT}\n", text)
        self.assertIn("Inputs: a/y.md\n", text)

    def test_research_task_with_consumer_passes_check(self):
        d = self.repo()
        self.init(d)
        self.assertEqual(self.p(d, *self.task_argv(1, "--type", "research", "--outputs", OUT, paths=OUT))[0], 0)
        self.assertEqual(self.p(d, *self.task_argv(2, "--depends", "1", paths="b/y.js"))[0], 0)
        Path(d, ".work", "s", "task-2.md").write_text(self.task_text(d, "task-2") + f"Read {OUT} first.\n", encoding="utf-8")
        rc, out, err = self.p(d, "check", "--stage", "s")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("OK 2 tasks", out)

    def test_research_task_without_consumer_fails_check(self):
        d = self.repo()
        self.init(d)
        self.assertEqual(self.p(d, *self.task_argv(1, "--type", "research", "--outputs", OUT, paths=OUT))[0], 0)
        rc, out, err = self.p(d, "check", "--stage", "s")
        self.assertEqual(rc, 2)
        self.assertRegex(out + err, r"research: task-(\d+): no later task depends on task-\1\b")


if __name__ == "__main__":
    unittest.main()
