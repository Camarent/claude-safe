import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import qrun
from tests.support.tmpdirs import tracked_mkdtemp


def _child_tmpdir(parent_tmpdir=None):
    """Run a command that prints its TMPDIR; return that value."""
    log = os.path.join(tracked_mkdtemp(), "q.log")
    env = {"TMPDIR": parent_tmpdir} if parent_tmpdir else {}
    with unittest.mock.patch.dict(os.environ, env):
        lines, rc = qrun._run(["sh", "-c", 'printf %s "$TMPDIR"'], log)
    assert rc == 0
    return lines[0]


class TestScratchBase(unittest.TestCase):
    def short_base(self):
        """A short dir under /tmp: a tracked dir is too deep for QRUN_TMP_MAX_PATH. Removed at test end."""
        d = tempfile.mkdtemp(prefix="qrt-", dir="/tmp")
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_scratch_is_created_under_the_base_dir(self):
        base = os.path.realpath(self.short_base())
        with unittest.mock.patch.object(qrun, "QRUN_TMP_BASE", base):
            child = _child_tmpdir()
        self.assertTrue(child.startswith(base + os.sep), child)

    def test_missing_base_dir_falls_back_to_the_tempfile_default(self):
        base = os.path.join(self.short_base(), "no-such-dir")
        with unittest.mock.patch.object(qrun, "QRUN_TMP_BASE", base):
            child = _child_tmpdir()
        self.assertFalse(child.startswith(base), child)
        self.assertFalse(os.path.exists(child), "scratch must be removed after the run")

    def test_too_long_scratch_path_falls_back(self):
        base = os.path.realpath(self.short_base())
        with (
            unittest.mock.patch.object(qrun, "QRUN_TMP_BASE", base),
            unittest.mock.patch.object(qrun, "QRUN_TMP_MAX_PATH", 5),
        ):
            child = _child_tmpdir()
        self.assertFalse(child.startswith(base), child)

    def test_long_parent_tmpdir_still_gives_a_short_child_tmpdir(self):
        long_tmp = "/" + "x" * 119  # a 120-char TMPDIR in the parent env
        with unittest.mock.patch.object(qrun, "QRUN_TMP_BASE", "/tmp"):
            child = _child_tmpdir(parent_tmpdir=long_tmp)
        self.assertLessEqual(len(child), qrun.QRUN_TMP_MAX_PATH, child)
        self.assertNotEqual(child, long_tmp)

    def test_scratch_dir_is_removed_after_the_run(self):
        base = os.path.realpath(self.short_base())
        with unittest.mock.patch.object(qrun, "QRUN_TMP_BASE", base):
            child = _child_tmpdir()
        self.assertFalse(os.path.exists(child))
        self.assertEqual(os.listdir(base), [])


if __name__ == "__main__":
    unittest.main()
