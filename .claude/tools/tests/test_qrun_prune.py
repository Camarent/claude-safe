import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import qrun


class TestPruneOldLogs(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def _make(self, name, age):
        p = os.path.join(self.d, name)
        open(p, "w").close()
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_old_removed_recent_and_foreign_kept(self):
        old = self._make("qrun-1.log", qrun.LOG_MAX_AGE + 60)
        new = self._make("qrun-2.log", 60)
        other = self._make("other-3.log", qrun.LOG_MAX_AGE + 60)
        qrun._prune_old_logs(self.d, min_keep=0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(other))

    def test_symlink_not_followed_and_missing_dir_ok(self):
        target = self._make("keep.txt", qrun.LOG_MAX_AGE + 60)
        link = os.path.join(self.d, "qrun-9.log")
        os.symlink(target, link)
        qrun._prune_old_logs(self.d)
        self.assertTrue(os.path.exists(target))
        qrun._prune_old_logs(os.path.join(self.d, "missing"))

    def _fill(self, fresh, old):
        day = qrun.LOG_MAX_AGE
        names = [(f"qrun-f{i}.log", 60 + i) for i in range(fresh)]
        names += [(f"qrun-o{i}.log", day + 60 + i) for i in range(old)]
        for n, a in names:
            self._make(n, a)
        qrun._prune_old_logs(self.d)
        return set(os.listdir(self.d))

    def test_fresh_capped_to_max_count(self):
        for n in range(50):
            self._make(f"qrun-f{n}.log", 60 + n)
        qrun._prune_old_logs(self.d, max_count=20)
        left = set(os.listdir(self.d))
        self.assertEqual(left, {f"qrun-f{i}.log" for i in range(20)})

    def test_fresh_under_cap_kept(self):
        self.assertEqual(len(self._fill(50, 0)), 50)

    def test_old_keep_20_newest(self):
        left = self._fill(0, 30)
        self.assertEqual(left, {f"qrun-o{i}.log" for i in range(20)})

    def test_few_old_all_kept(self):
        self.assertEqual(len(self._fill(0, 5)), 5)

    def test_mix_fresh_plus_newest_old(self):
        left = self._fill(3, 40)
        want = {f"qrun-f{i}.log" for i in range(3)} | {f"qrun-o{i}.log" for i in range(17)}
        self.assertEqual(left, want)


if __name__ == "__main__":
    unittest.main()
