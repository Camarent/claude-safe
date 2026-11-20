import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import commit
from tests.support.tmpdirs import tracked_tmpdir


def git(d, *args):
    return subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, check=False)


class CommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        git(self.d, "init")
        git(self.d, "config", "user.name", "T")
        git(self.d, "config", "user.email", "t@example.com")
        git(self.d, "config", "commit.gpgsign", "false")
        with open(os.path.join(self.d, "f.txt"), "w") as f:
            f.write("x")
        git(self.d, "add", "f.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_subject_has_trailer(self):
        rc = commit.main(["--subject", "feat(tools): add x", "--body", "why", "--dir", self.d])
        self.assertEqual(rc, 0)
        msg = git(self.d, "log", "-1", "--format=%B").stdout
        self.assertTrue(msg.startswith("feat(tools): add x"))
        self.assertIn(commit.trailer(), msg)

    def test_trailer_from_the_workspace_config(self):
        ws = tempfile.TemporaryDirectory()
        self.addCleanup(ws.cleanup)
        Path(ws.name, "pipeline.json").write_text('{"commit_trailer": "Co-Authored-By: X <x@x>"}')
        self.assertEqual(commit.trailer(ws.name), "Co-Authored-By: X <x@x>")
        self.assertEqual(commit.main(["--subject", "feat(MyFeature): add x", "--dir", self.d, "--workspace", ws.name]), 0)
        self.assertIn("Co-Authored-By: X <x@x>", git(self.d, "log", "-1", "--format=%B").stdout)

    def test_revert_and_reapply_subjects_accepted(self):
        for subj in ('Revert "feat(tools): add x"', 'Reapply "feat(tools): add x"'):
            Path(self.d, "f.txt").write_text(subj)
            git(self.d, "add", "-A")
            self.assertEqual(commit.main(["--subject", subj, "--dir", self.d]), 0)
            self.assertTrue(git(self.d, "log", "-1", "--format=%s").stdout.startswith(subj[:6]))
        self.assertNotEqual(commit.main(["--subject", "Revert x", "--dir", self.d]), 0)

    def test_subject_ok_is_strict(self):
        for bad in ("feat(x): add y\n", 'Revert "x"', 'Revert "Revert x"', 'Reapply "nope: x"', "add x"):
            self.assertFalse(commit.subject_ok(bad), bad)
        self.assertTrue(commit.subject_ok('Revert "Revert "fix(a): b c""'))

    def test_bad_subject_refused(self):
        rc = commit.main(["--subject", "add x", "--dir", self.d])
        self.assertNotEqual(rc, 0)
        self.assertNotEqual(git(self.d, "rev-parse", "HEAD").returncode, 0)


if __name__ == "__main__":
    unittest.main()
