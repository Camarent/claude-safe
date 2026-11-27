"""cs-devtools runs one tool of the AUDIT.md audit-tools block over given paths; any other name is refused with exit 2. Fake tools, no real audit tool."""

import glob
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import dev_tools
from tests.support.tmpdirs import tracked_mkdtemp

WRAPPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "bin", "cs-devtools")
FAKE_BLOCK = """# AUDIT

```audit-tools
fake | . | printf 'clean\\n' | vulture
noisy | . | printf 'a.py:1: unused thing (90%% confidence)\\n' | vulture
echo | . | echo | vulture
```
"""


class DevToolsTest(unittest.TestCase):
    def setUp(self):
        self.repo = tracked_mkdtemp(prefix="devtools-repo-")
        with open(os.path.join(self.repo, "AUDIT.md"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_BLOCK)
        here = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, here)

    def raw_logs(self):
        return glob.glob(os.path.join(self.repo, ".audits", "raw", "*.log"))

    def test_tool_outside_block_is_refused_and_not_run(self):
        self.assertEqual(dev_tools.main(["rm"]), 2)
        self.assertEqual(self.raw_logs(), [])

    def test_no_tool_name_is_usage_error(self):
        self.assertEqual(dev_tools.main([]), 2)

    def test_clean_fake_tool_exits_zero(self):
        self.assertEqual(dev_tools.main(["fake"]), 0)
        self.assertEqual(len(self.raw_logs()), 1)

    def test_fake_tool_with_findings_exits_one(self):
        self.assertEqual(dev_tools.main(["noisy"]), 1)

    def test_given_paths_are_appended_to_the_tool_command(self):
        self.assertEqual(dev_tools.main(["echo", "src/a b.py"]), 0)
        with open(self.raw_logs()[0], encoding="utf-8") as fh:
            self.assertIn("src/a b.py", fh.read())

    def test_wrapper_refuses_unknown_tool_with_exit_two(self):
        p = subprocess.run([WRAPPER, "rm"], cwd=self.repo, capture_output=True, text=True, check=False)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("REFUSED", p.stderr)

    def test_wrapper_runs_fake_tool(self):
        p = subprocess.run([WRAPPER, "fake"], cwd=self.repo, capture_output=True, text=True, check=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OK fake", p.stdout)


if __name__ == "__main__":
    unittest.main()
