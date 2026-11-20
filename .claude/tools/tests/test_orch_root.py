"""orch.py retry|skip find .work from the stage root (walk up), or refuse with a clear message."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch
from pipeline.util import stage_root
from tests.support.tmpdirs import tracked_mkdtemp


class OrchRootTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tracked_mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".work", "s"))
        self.nested = os.path.join(self.root, "course")
        os.makedirs(self.nested)
        subprocess.run(["git", "-C", self.nested, "init", "-q"], check=True)  # a real nested repo
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.nested)

    def run_orch(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_orch.main(list(args))
        return out.getvalue()

    def test_skip_from_a_nested_repo_writes_to_the_stage_root(self):
        out = self.run_orch("skip", "--stage", "s", "--step", "ops-1", "--dir", self.nested)
        self.assertEqual(json.loads(out)["recorded"], "skip")
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".work", "s", "orch.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.nested, ".work")))

    def test_unknown_stage_is_refused_with_a_message(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_orch("retry", "--stage", "nope", "--step", "task-1", "--dir", self.nested)
        self.assertIn("no .work/nope", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.nested, ".work")))

    def test_stage_root(self):
        self.assertEqual(stage_root(self.nested, "s"), self.root)
        self.assertIsNone(stage_root(self.nested, "zzz"))

    def test_stage_root_refuses_bad_names_and_stops_at_pipeline_json(self):
        for bad in ("..", "../s", "/etc", "a/b", "s/phases/x", ""):
            self.assertIsNone(stage_root(self.nested, bad), bad)
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.makedirs(os.path.join(self.root, "ws"))
        with open(os.path.join(self.root, "ws", "pipeline.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.makedirs(os.path.join(self.root, ".work", "t"))
        os.chdir(os.path.join(self.root, "ws"))
        self.assertIsNone(stage_root(os.path.join(self.root, "ws"), "t"))


if __name__ == "__main__":
    unittest.main()
