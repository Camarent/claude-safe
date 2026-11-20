"""Phase start base: audit context and recall measure a feature phase from the work-branch head at its start."""

import argparse
import os
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, cli_feature
from tests.support.tmpdirs import tracked_mkdtemp


class PhaseBaseTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        g = ["git", "-C", self.d, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*g, "init", "-q", "-b", "stage/F"], check=True)
        subprocess.run([*g, "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        self.a = argparse.Namespace(dir=self.d, stage="F/phases/02-x")
        self.cfg = {"branch_release": "master", "branch_work": "dev"}

    def test_falls_back_to_release_branch_without_a_base_file(self):
        self.assertEqual(audit_phases.audit_base(self.a, self.cfg), "master")

    def test_record_writes_once_and_audit_base_reads_it(self):
        folder = os.path.join(self.d, ".work", "F", "phases", "02-x")
        stage_dir = os.path.join(self.d, ".work", self.a.stage)
        self.assertEqual(os.path.abspath(folder), os.path.abspath(stage_dir))
        os.makedirs(folder)
        cli_feature._record_base(self.a, folder, {"dir": "02-x"})
        sha = subprocess.run(["git", "-C", self.d, "rev-parse", "stage/F"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(audit_phases.audit_base(self.a, self.cfg), sha)
        pathlib.Path(folder, "base.txt").write_text("keep\n")
        cli_feature._record_base(self.a, folder, {"dir": "02-x"})
        self.assertEqual(audit_phases.audit_base(self.a, self.cfg), "keep")

    def test_finish_phase_keeps_the_release_base(self):
        folder = os.path.join(self.d, ".work", "F", "phases", "06-finish")
        os.makedirs(folder)
        cli_feature._record_base(self.a, folder, {"dir": "06-finish"})
        self.assertFalse(os.path.exists(os.path.join(folder, "base.txt")))


if __name__ == "__main__":
    unittest.main()
