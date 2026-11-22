import os
import shutil
import subprocess
import sys
import time
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import qrun  # noqa: E402  (import after the sys.path insert)
from pipeline import cleanup  # noqa: E402  (import after the sys.path insert)
from pipeline.configuration import (  # noqa: E402  (import after the sys.path insert)
    CLEANUP_PTEST_MIN_AGE_S,
    CLEANUP_PTEST_PREFIX,
    CLEANUP_QRUN_TMP_MIN_AGE_S,
    QRUN_LOG_MAX_COUNT,
    QRUN_TMP_PREFIX,
)
from pipeline.scratch import dead_owner_dirs, owner_pid  # noqa: E402  (import after the sys.path insert)
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402  (import after the sys.path insert)

LATER = 10**10  # a clock far past every mtime: every temp dir counts as old


def dead_pid():
    """The pid of a child that has already exited: no live process has it now."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class DeadOwnerTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def test_owner_pid_reads_the_pid_of_the_name(self):
        self.assertEqual(owner_pid(f"{QRUN_TMP_PREFIX}123-abc", QRUN_TMP_PREFIX), 123)
        self.assertIsNone(owner_pid(f"{QRUN_TMP_PREFIX}abc-1", QRUN_TMP_PREFIX))
        self.assertIsNone(owner_pid(f"{QRUN_TMP_PREFIX}0-x", QRUN_TMP_PREFIX))
        self.assertIsNone(owner_pid("other-123-abc", QRUN_TMP_PREFIX))

    def test_dead_owner_dirs_lists_only_dead_pids(self):
        dead = os.path.join(self.d, f"{QRUN_TMP_PREFIX}{dead_pid()}-x")
        live = os.path.join(self.d, f"{QRUN_TMP_PREFIX}{os.getpid()}-y")
        os.makedirs(dead)
        os.makedirs(live)
        self.assertEqual(dead_owner_dirs(self.d, QRUN_TMP_PREFIX), [dead])

    def test_prune_dead_scratch_removes_dead_keeps_live_and_files(self):
        dead = os.path.join(self.d, f"{QRUN_TMP_PREFIX}{dead_pid()}-x")
        live = os.path.join(self.d, f"{QRUN_TMP_PREFIX}{os.getpid()}-y")
        plain = os.path.join(self.d, f"{QRUN_TMP_PREFIX}{dead_pid()}-file")
        os.makedirs(dead)
        os.makedirs(live)
        open(plain, "w").close()

        qrun._prune_dead_scratch([self.d, os.path.join(self.d, "missing")])

        self.assertFalse(os.path.exists(dead))
        self.assertTrue(os.path.isdir(live))
        self.assertTrue(os.path.isfile(plain))  # only directories are scratch dirs

    def test_new_scratch_name_carries_this_pid(self):
        with unittest.mock.patch.object(qrun, "QRUN_TMP_BASE", self.d), unittest.mock.patch.object(qrun, "QRUN_TMP_MAX_PATH", 10**6):
            scratch = qrun._new_scratch()
        self.addCleanup(shutil.rmtree, scratch, True)
        self.assertEqual(os.path.dirname(scratch), self.d)
        self.assertEqual(owner_pid(os.path.basename(scratch), QRUN_TMP_PREFIX), os.getpid())
        self.assertIn(os.path.basename(scratch), os.listdir(self.d))


class LogCapTest(unittest.TestCase):
    def test_log_cap_is_one_hundred_fresh_logs(self):
        self.assertEqual(QRUN_LOG_MAX_COUNT, 100)
        d = tracked_mkdtemp()
        for n in range(120):
            p = os.path.join(d, f"qrun-f{n}.log")
            open(p, "w").close()
            t = time.time() - 60 - n
            os.utime(p, (t, t))
        qrun._prune_old_logs(d)
        self.assertEqual(len(os.listdir(d)), 100)


class CleanupScratchTest(unittest.TestCase):
    def setUp(self):
        self.t = tracked_mkdtemp()  # the project dir
        self.tmp = tracked_mkdtemp()  # the temp root cleanup sweeps
        os.makedirs(f"{self.t}/.work/s/tmp")

    def _dir(self, base, name):
        p = os.path.join(base, name)
        os.makedirs(p)
        return p

    def test_dead_pid_qrun_tmp_removed_when_old(self):
        dead = self._dir(self.tmp, f"{QRUN_TMP_PREFIX}{dead_pid()}-a")

        res = cleanup.cleanup(self.t, tmp_root=self.tmp, now=LATER)

        self.assertIn(dead, res["tmp"])
        self.assertFalse(os.path.exists(dead))

    def test_live_pid_and_fresh_qrun_tmp_kept(self):
        live = self._dir(self.tmp, f"{QRUN_TMP_PREFIX}{os.getpid()}-a")
        fresh = self._dir(self.tmp, f"{QRUN_TMP_PREFIX}{dead_pid()}-b")
        now = os.path.getmtime(fresh) + CLEANUP_QRUN_TMP_MIN_AGE_S - 1

        res = cleanup.cleanup(self.t, tmp_root=self.tmp, now=now)

        self.assertEqual(res["tmp"], [])
        self.assertTrue(os.path.isdir(live))
        self.assertTrue(os.path.isdir(fresh))

    def test_ptest_dirs_under_work_stage_tmp_removed_when_old(self):
        old = self._dir(f"{self.t}/.work/s/tmp", f"{CLEANUP_PTEST_PREFIX}1")
        other = self._dir(f"{self.t}/.work/s/tmp", "keep-me")

        dry = cleanup.cleanup(self.t, tmp_root=self.tmp, dry_run=True, now=LATER)
        self.assertEqual(dry["tmp"], [old])
        self.assertTrue(os.path.isdir(old))  # dry run deletes nothing

        res = cleanup.cleanup(self.t, tmp_root=self.tmp, now=LATER)

        self.assertEqual(res["tmp"], [old])
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(other))  # no ptest prefix: not a scratch dir of a test run

    def test_recent_ptest_dir_kept(self):
        recent = self._dir(f"{self.t}/.work/s/tmp", f"{CLEANUP_PTEST_PREFIX}3")
        now = os.path.getmtime(recent) + CLEANUP_PTEST_MIN_AGE_S - 1

        res = cleanup.cleanup(self.t, tmp_root=self.tmp, now=now)

        self.assertEqual(res["tmp"], [])
        self.assertTrue(os.path.isdir(recent))


if __name__ == "__main__":
    unittest.main()
