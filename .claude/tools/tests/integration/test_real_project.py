"""Integration test on a REAL project's pipeline settings. Opt in with PIPELINE_PROJECT_DIR=<project root> (scripts/dev.sh itest).

Read-only for the project: only pipeline.json and the audit file are copied into a temp dir, then the stage walker runs there."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline import configuration
from tests.support.project import TABLE, dry_run, roles

PROJECT = os.environ.get("PIPELINE_PROJECT_DIR", "")


@unittest.skipUnless(
    PROJECT and os.path.isfile(os.path.join(PROJECT, "pipeline.json")), "integration: set PIPELINE_PROJECT_DIR to a project with pipeline.json"
)
class RealProjectTest(unittest.TestCase):
    src: dict

    @classmethod
    def setUpClass(cls):
        cls.src = json.loads(Path(os.path.join(PROJECT, "pipeline.json")).read_text(encoding="utf-8"))
        if "full_test" in cls.src and "gates" not in cls.src:
            raise unittest.SkipTest("project pipeline.json still uses `full_test`; migrate it to `gates`")

    def copy_project(self, flags="no-retro"):
        d = tempfile.mkdtemp(prefix="real-project-copy-")
        shutil.copy(os.path.join(PROJECT, "pipeline.json"), d)
        repo = self.src.get("repo", ".")
        audit = os.path.join(PROJECT, repo, self.src.get("audit_file", "AUDIT.md"))
        if os.path.isfile(audit):
            os.makedirs(os.path.join(d, repo), exist_ok=True)
            shutil.copy(audit, os.path.join(d, repo))
        os.makedirs(os.path.join(d, ".work", "s"))
        with open(os.path.join(d, ".work", "s", "TASKS.md"), "w", encoding="utf-8") as f:
            f.write(TABLE + f"flags: {flags}\n")
        return d

    def test_settings_load_with_the_project_values(self):
        cfg = configuration.project(self.copy_project())
        self.assertEqual(cfg["branch_work"], self.src.get("branch_work", cfg["branch_work"]))
        self.assertEqual(cfg["branch_release"], self.src.get("branch_release", cfg["branch_release"]))

    def test_stage_walks_to_the_end_on_the_real_settings(self):
        rc, steps, _ = dry_run(self.copy_project())
        self.assertEqual(rc, 0)
        r = roles(steps)
        self.assertEqual(r[0], "dev")
        merge = next(i for i, s in enumerate(steps) if s[1] == "merger" or (s[1] == "script" and "merge_wave.py" in s[3]))  # worktree projects merge by script
        self.assertLess(r.index("reviewer"), merge)
        if self.src.get("prereview"):  # opt-in gate: a script step runs between dev and the paid reviewer
            self.assertTrue(any("prereview.py" in s[3] for s in steps if s[1] == "script"))

    def test_gates_and_each_become_the_script_check(self):
        if "gates" not in self.src:
            self.skipTest("project has no gates")
        _, steps, _ = dry_run(self.copy_project())
        self.assertTrue(any("final_check.py" in s[3] for s in steps if s[1] == "script"))


if __name__ == "__main__":
    unittest.main()
