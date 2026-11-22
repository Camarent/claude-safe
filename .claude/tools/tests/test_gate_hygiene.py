"""Secret scan of the task diff (gate_secrets.scan_diff): a fake AWS key in a changed file is a finding, a clean file is not, no gitleaks is a
skip, the temp copy is removed, and a mutation of scan_diff to `return []` would fail the finding test."""

import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
from pipeline import gate_paths as P  # noqa: E402 - sys.path.insert above sets TOOLS
from pipeline import gate_secrets as G  # noqa: E402 - sys.path.insert above sets TOOLS
from tests.support.binaries import requires_binary  # noqa: E402 - sys.path.insert above sets TOOLS

IDENT = ("-c", "user.name=t", "-c", "user.email=t@t")
# fake AWS access key id, assembled from parts so the file holds no literal key
FAKE_KEY = "AKIA" + "Q3ZX7LBW" + "M2TYNKPD"


def git(cwd, *args):
    return subprocess.run(["git", *IDENT, "-C", cwd, *args], capture_output=True, text=True, check=True).stdout


class TestScanDiff(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        git(self.root, "init", "-q")
        with open(os.path.join(self.root, "base.txt"), "w", encoding="utf-8") as f:
            f.write("base\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    @requires_binary("gitleaks")
    def test_fake_key_in_changed_file_is_a_finding(self):
        self.write("creds.txt", f"aws_key = {FAKE_KEY}\n")
        found = G.scan_diff(self.root, "HEAD")
        self.assertTrue(found)
        self.assertTrue(any(line.startswith("creds.txt:") for line in found), found)
        self.assertFalse(any(FAKE_KEY in line for line in found), "the secret must not appear in a finding")

    def test_clean_changed_file_is_no_finding(self):
        self.write("notes.txt", "nothing secret here\n")
        self.assertEqual(G.scan_diff(self.root, "HEAD"), [])

    def test_unchanged_file_with_key_is_not_scanned(self):
        # only files changed vs base are copied: a key committed before base is not in the diff
        self.write("old.txt", f"aws_key = {FAKE_KEY}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "old")
        self.assertEqual(G.scan_diff(self.root, "HEAD"), [])

    def test_gitleaks_missing_is_no_finding(self):
        self.write("creds.txt", f"aws_key = {FAKE_KEY}\n")
        empty_bin = tempfile.TemporaryDirectory()
        self.addCleanup(empty_bin.cleanup)
        with unittest.mock.patch.dict(os.environ, {"PATH": empty_bin.name}):
            self.assertEqual(G.scan_diff(self.root, "HEAD"), [])

    @requires_binary("gitleaks")
    def test_temp_copy_is_removed(self):
        self.write("creds.txt", f"aws_key = {FAKE_KEY}\n")
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def recording(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            made.append(path)
            return path

        with unittest.mock.patch.object(G.tempfile, "mkdtemp", side_effect=recording):
            G.scan_diff(self.root, "HEAD")
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))


class TestPathDependentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        git(self.root, "init", "-q")
        self.write("base.txt", "base\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    def commit_then_scan(self, name, text):
        # base first, then the changed file: the scan sees it in `git diff base`
        self.write(name, text)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "change")
        return P.path_dependent_tests(self.root, "HEAD~1")

    def test_assert_on_task_id_literal_is_flagged(self):
        found = self.commit_then_scan("test_paths.py", 'def test_x(self):\n    self.assertIn("task-3", out)\n')
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("test_paths.py:2:"), found)

    def test_plain_assignment_of_job_path_is_not_flagged(self):
        found = self.commit_then_scan("test_paths.py", 'PATH = "/job/task-3/claude-safe"\n')
        self.assertEqual(found, [])

    def test_non_test_file_is_not_scanned(self):
        found = self.commit_then_scan("helper.py", 'def f(self):\n    self.assertIn("task-3", out)\n')
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
