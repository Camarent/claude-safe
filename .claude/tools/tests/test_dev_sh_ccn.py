"""scripts/dev.sh ccn: an optional -C N and PATHs replace the default limit 15 and .claude/tools."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEV_SH = os.path.join(ROOT, "scripts", "dev.sh")

# CCN 13: one function with 12 if-statements (1 + 12 decision points)
CCN13 = "def busy(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12)) + "    return -1\n"


class CcnArgsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_mkdtemp()
        with open(os.path.join(self.tmp, "busy.py"), "w", encoding="utf-8") as f:
            f.write(CCN13)

    def _ccn(self, *args):
        return subprocess.run([DEV_SH, "ccn", *args], cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)

    def test_limit_15_passes_ccn13(self):
        r = self._ccn("-C", "15", self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_limit_12_fails_ccn13(self):
        r = self._ccn("-C", "12", self.tmp)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("busy", r.stdout)


if __name__ == "__main__":
    unittest.main()
