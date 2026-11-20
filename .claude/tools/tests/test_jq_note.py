"""Every role brief tells agents to read JSON with jq; the jq recipes in practices/bash.md use only plain-shell tools."""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import briefs  # noqa: E402  (path set above)
from pipeline.configuration import PLAIN_SHELL_TOOLS  # noqa: E402  (path set above)

BASH_MD = os.path.join(os.path.dirname(ROOT), "practices", "bash.md")


class JqNoteTest(unittest.TestCase):
    def test_bash_note_mentions_jq(self):
        self.assertIn("`jq`", briefs.BASH_NOTE)
        self.assertIn("practices/bash.md", briefs.BASH_NOTE)

    def test_recipes_use_plain_tools_only(self):
        with open(BASH_MD, encoding="utf-8") as f:
            text = f.read()
        sec = text.split("## JSON with jq", 1)[1].split("\n## ", 1)[0]
        cmds = [c for c in re.findall(r"`(jq [^`]+)`", sec) if " f.json" in c or "f.jsonl" in c or "B.json" in c]
        self.assertGreaterEqual(len(cmds), 6)
        for c in cmds:
            for seg in c.split(" | "):
                self.assertIn(seg.split()[0], PLAIN_SHELL_TOOLS, c)
            self.assertNotIn(" > ", c)


if __name__ == "__main__":
    unittest.main()
