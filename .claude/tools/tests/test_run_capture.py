"""lpcall.run_rc/sh and localdev.sh keep their error handling behind util.run_capture."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import localdev
from search import lpcall
from tests.support.tmpdirs import tracked_mkdtemp

PY = sys.executable


class RunCaptureTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def test_run_rc_ok_and_nonzero(self):
        self.assertEqual(lpcall.run_rc([PY, "-c", "print('hi')"], self.d), (0, "hi\n"))
        self.assertEqual(lpcall.run_rc([PY, "-c", "import sys; print('o'); sys.exit(3)"], self.d), (3, "o\n"))

    def test_run_rc_swallows_missing_binary_bad_cwd_and_timeout(self):
        self.assertEqual(lpcall.run_rc(["no-such-binary-xyz"], self.d), (None, ""))
        self.assertEqual(lpcall.run_rc([PY, "-c", "pass"], os.path.join(self.d, "gone")), (None, ""))
        self.assertEqual(lpcall.run_rc([PY, "-c", "import time; time.sleep(5)"], self.d, t=0.2), (None, ""))

    def test_sh_stdout_only_on_exit_zero(self):
        self.assertEqual(lpcall.sh([PY, "-c", "print('x')"], self.d), "x\n")
        self.assertEqual(lpcall.sh([PY, "-c", "import sys; print('x'); sys.exit(1)"], self.d), "")

    def test_localdev_sh_returns_completed_process_and_adds_git_env(self):
        p = localdev.sh([PY, "-c", "import os; print(os.environ['GIT_CONFIG_KEY_0'])"], self.d)
        self.assertEqual((p.returncode, p.stdout), (0, "safe.directory\n"))
        p = localdev.sh([PY, "-c", "import os; print(os.environ['K'])"], self.d, env={"K": "v", "PATH": os.environ["PATH"]})
        self.assertEqual(p.stdout, "v\n")
        self.assertEqual(localdev.sh([PY, "-c", "import sys; sys.exit(2)"], self.d).returncode, 2)

    def test_localdev_sh_raises_where_lpcall_swallows(self):
        with self.assertRaises(OSError):
            localdev.sh(["no-such-binary-xyz"], self.d)
        with self.assertRaises(subprocess.TimeoutExpired):
            localdev.sh([PY, "-c", "import time; time.sleep(5)"], self.d, timeout=0.2)


if __name__ == "__main__":
    unittest.main()
