"""Predicted paths are soft scope: dev never blocks on them, reviewer never fails on them, neither mentions "minimal"."""

import os
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents")


def read(name):
    with open(os.path.join(AGENTS, name), encoding="utf-8") as f:
        return f.read()


class AgentScopeRule(unittest.TestCase):
    def test_dev_never_blocks_on_paths(self):
        text = read("dev.md")
        self.assertIn("never a reason to return BLOCKED or ESCALATE", text)

    def test_reviewer_never_fails_on_paths(self):
        text = read("reviewer.md")
        self.assertIn("never FAIL for being outside the predicted paths", text)

    def test_no_minimal_in_scope_rules(self):
        for name in ("dev.md", "reviewer.md"):
            self.assertNotIn("minimal", read(name).lower(), name)


if __name__ == "__main__":
    unittest.main()
