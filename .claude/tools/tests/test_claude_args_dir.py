"""claude_args: allowedTools and the ops rm pattern come from the run dir, not the process cwd."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude
from tests.support.tmpdirs import tracked_mkdtemp


class RunDirTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"allow": ["Bash(x validate *)"]}, f)

    def test_project_allow_reads_given_dir(self):
        self.assertEqual(claude.project_allow(self.d), ["--allowedTools", "Bash(x validate *)"])

    def test_claude_args_uses_run_dir_for_allow(self):
        args = claude.claude_args("dev", None, None, "p", dir=self.d)
        self.assertIn("Bash(x validate *)", args)

    def test_ops_rm_pattern_uses_run_dir(self):
        args = claude.claude_args("ops", None, None, "p", dir=self.d)
        self.assertIn(f"Bash(rm {self.d}/.work/*)", args)
        self.assertNotIn(f"Bash(rm {os.getcwd()}/.work/*)", args)

    def test_scratch_script_allow_for_stage_and_phase_dirs(self):
        stage, phase = f"{self.d}/.work/s/tmp", f"{self.d}/.work/feat/phases/03-x/tmp"
        for scratch in (stage, phase):
            args = claude.claude_args("dev", None, None, "p", dir=self.d, scratch=scratch)
            self.assertIn(f"Bash(python3 -I {scratch}/*)", args)
            self.assertIn("--allowedTools", args)
        args = claude.claude_args("dev", None, None, "p", dir=self.d, scratch=stage)
        self.assertNotIn(f"Bash(python3 -I {phase}/*)", args)  # no other dir
        self.assertEqual(claude.scratch_allow(None), ())

    def test_bash_allow_override_drops_scratch_allow(self):
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"bash_allow": ["ls *"]}, f)
        args = claude.claude_args("dev", None, None, "p", dir=self.d, scratch=f"{self.d}/.work/s/tmp")
        self.assertFalse(any("python3 -I" in a and ".work" in a for a in args))


if __name__ == "__main__":
    unittest.main()
