"""ci_clean: remove only the untracked files a CI command created; tracked and pre-existing files stay."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import ci_clean


def _write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class CiCleanTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        subprocess.run(["git", "init", "-q", self.d], check=True)
        _write(os.path.join(self.d, "tracked.txt"), "one\n")
        subprocess.run(["git", "-C", self.d, "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", self.d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)

    def _path(self, rel):
        return os.path.join(self.d, *rel.split("/"))

    def test_snapshot_lists_untracked_not_tracked(self):
        _write(self._path("pre.txt"))
        self.assertEqual(ci_clean.snapshot(self.d), {"pre.txt"})

    def test_file_created_after_snapshot_is_removed(self):
        before = ci_clean.snapshot(self.d)
        _write(self._path("new.txt"))
        self.assertEqual(ci_clean.remove_new(self.d, before), ["new.txt"])
        self.assertFalse(os.path.exists(self._path("new.txt")))

    def test_pre_existing_untracked_file_is_kept(self):
        _write(self._path("pre.txt"))
        before = ci_clean.snapshot(self.d)
        _write(self._path("new.txt"))
        ci_clean.remove_new(self.d, before)
        self.assertTrue(os.path.exists(self._path("pre.txt")))

    def test_modified_tracked_file_is_kept(self):
        before = ci_clean.snapshot(self.d)
        _write(self._path("tracked.txt"), "changed\n")
        self.assertEqual(ci_clean.remove_new(self.d, before), [])
        with open(self._path("tracked.txt")) as f:
            self.assertEqual(f.read(), "changed\n")

    def test_requirements_file_written_by_shell_is_removed(self):
        before = ci_clean.snapshot(self.d)
        subprocess.run(["sh", "-c", "echo 'pkg==1' > requirements-ci.txt"], cwd=self.d, check=True)
        self.assertEqual(ci_clean.remove_new(self.d, before), ["requirements-ci.txt"])
        self.assertFalse(os.path.exists(self._path("requirements-ci.txt")))

    def test_new_untracked_dir_with_files_is_removed(self):
        before = ci_clean.snapshot(self.d)
        _write(self._path("build/out/a.txt"))
        _write(self._path("build/b.txt"))
        self.assertEqual(ci_clean.remove_new(self.d, before), ["build/b.txt", "build/out/a.txt"])
        self.assertFalse(os.path.exists(self._path("build")))

    def test_symlink_pointing_outside_is_skipped(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        _write(os.path.join(outside, "victim.txt"))
        before = ci_clean.snapshot(self.d)
        os.symlink(outside, self._path("link"))
        os.symlink(os.path.join(outside, "victim.txt"), self._path("victim-link.txt"))
        ci_clean.remove_new(self.d, before)
        self.assertTrue(os.path.exists(os.path.join(outside, "victim.txt")))


if __name__ == "__main__":
    unittest.main()
