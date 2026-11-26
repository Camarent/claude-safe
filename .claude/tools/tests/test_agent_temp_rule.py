"""Every agent with a Write or Bash tool carries the temp-file rule; briefs name the scratchpad."""

import os
import re
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs
from pipeline.configuration import AGENTS_DIR, SCRATCH_DIR

RULE = "only under the scratchpad directory named in your brief"
NAMED = ("scout", "tester", "ops", "web-tester", "ux-reviewer")


def tools_of(text):
    m = re.search(r"^tools:\s*(.*)$", text, re.MULTILINE)
    return [t.strip() for t in m.group(1).split(",")] if m else []


class AgentTempRule(unittest.TestCase):
    def test_named_agents_have_the_rule(self):
        for name in NAMED:
            with open(os.path.join(AGENTS_DIR, f"{name}.md"), encoding="utf-8") as f:
                self.assertTrue(RULE in f.read(), name)

    def test_every_agent_with_write_or_bash_has_the_rule(self):
        for fn in sorted(os.listdir(AGENTS_DIR)):
            if not fn.endswith(".md"):
                continue
            with open(os.path.join(AGENTS_DIR, fn), encoding="utf-8") as f:
                text = f.read()
            if {"Write", "Bash"} & set(tools_of(text)):
                self.assertTrue(RULE in text, fn)

    def test_brief_names_the_scratchpad(self):
        t = briefs.tail(SimpleNamespace(stage="s"), "a1", "DONE")
        self.assertIn(f".work/s/{SCRATCH_DIR}/", t)


if __name__ == "__main__":
    unittest.main()
