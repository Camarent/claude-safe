"""Runbook practices: the driver runbook and the lead rules exist, ship with the installer, and the prepare-repo template links both."""

import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PRACTICES = os.path.join(REPO, ".claude", "practices")
INSTALLER = os.path.join(REPO, "scripts", "install-claude-config.sh")
RUNBOOK_FILES = ("driver-runbook.md", "lead-rules.md")

sys.path.insert(0, os.path.join(REPO, ".claude", "tools"))
from pipeline.configuration import GUIDELINES_RUNBOOK_LINES  # noqa: E402  sys.path set above: pipeline package lives in .claude/tools


def installer_skip_list():
    """-> the names in the CLAUDE_CONFIG_SKIP array of scripts/install-claude-config.sh."""
    with open(INSTALLER, encoding="utf-8") as fh:
        m = re.search(r"^CLAUDE_CONFIG_SKIP=\(([^)]*)\)", fh.read(), re.MULTILINE)
    if m is None:
        raise AssertionError("CLAUDE_CONFIG_SKIP not found")
    return m.group(1).split()


class RunbookPractices(unittest.TestCase):
    def test_both_practices_files_exist(self):
        for name in RUNBOOK_FILES:
            with self.subTest(name=name):
                self.assertTrue(os.path.isfile(os.path.join(PRACTICES, name)))

    def test_practices_dir_is_not_skipped_by_installer(self):
        # the installer syncs each top-level ~/.claude item; .claude/practices ships unless its name is in the skip list
        self.assertNotIn("practices", installer_skip_list())

    def test_template_links_both_files(self):
        for name in RUNBOOK_FILES:
            with self.subTest(name=name):
                self.assertTrue(any("{dir}/" + name in ln for ln in GUIDELINES_RUNBOOK_LINES))
        self.assertEqual(len(GUIDELINES_RUNBOOK_LINES), len(RUNBOOK_FILES))


if __name__ == "__main__":
    unittest.main()
