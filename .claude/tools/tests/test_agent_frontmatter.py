"""Every agent file has a valid model and a one-line description."""

import glob
import os
import re
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents")


class AgentFrontmatter(unittest.TestCase):
    def test_model_and_description(self):
        files = sorted(glob.glob(os.path.join(AGENTS, "*.md")))
        self.assertTrue(files)
        for f in files:
            head = open(f, encoding="utf-8").read().split("\n---", 1)[0]
            m = re.search(r"^model:\s*(\S+)$", head, re.M)
            self.assertIn(m and m.group(1), {"haiku", "sonnet", "opus"}, f)
            self.assertRegex(head, r"(?m)^description: \S", f)
            self.assertRegex(head, r"(?m)^name: " + re.escape(os.path.basename(f)[:-3]) + "$", f)

    def test_commit_tool_matches_headless_allow_pattern(self):
        """The headless allow list is `python3 -I <tools>/*`: a bare `python3 .../commit.py` in an agent file is denied ('requires approval')."""
        for f in sorted(glob.glob(os.path.join(AGENTS, "*.md"))):
            text = open(f, encoding="utf-8").read()
            self.assertNotRegex(text, r"python3 (?!-I)\S*tools/commit\.py", f)


if __name__ == "__main__":
    unittest.main()
