"""Agent files stay under their recorded size caps; gate-rule keywords survive the split into <agent>/rare-rules.md."""

import glob
import os
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents")

# Recorded caps in bytes: the split files hold the rarely used rules, the main files stay small.
# architect.md: 27000 (was 26000) because task-8 moved four always-read brief rules into it; they must sit in the main file, not rare-rules.md.
# dev.md: 13700 (was 13500) and reviewer.md: 12300 (was 12200) because task-3 added the Bash rules line to both (and the CCN and tooling-BLOCKED lines to dev.md); they must sit in the main files, not rare-rules.md.
# dev.md: 14600 (was 13700) and reviewer.md: 13300 (was 12300) because task-8 put the E1-E4 escalation rules in both main files: test_escalation_docs.py requires them word for word in each doc, so they cannot move to rare-rules.md.
# architect.md: 27400 (was 27000) because the UI job rule bullet (glossary, sign-off, prototype) is always-read planning guidance; it sits in the main file, not rare-rules.md.
CAPS = {"architect.md": 27400, "dev.md": 14600, "reviewer.md": 13300}

# Gate-rule keywords that existed in the agent files before the split.
KEYWORDS = ("ccn", "mutation check", "PLAN GAP", "Predicted paths", "VERDICT")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def agent_text(name):
    """The agent file plus its split files (<agents>/<name>/*.md)."""
    stem = os.path.splitext(name)[0]
    parts = [read(os.path.join(AGENTS, name))]
    for path in sorted(glob.glob(os.path.join(AGENTS, stem, "*.md"))):
        parts.append(read(path))
    return "\n".join(parts)


class AgentRules(unittest.TestCase):
    def test_agent_files_under_recorded_size_caps(self):
        for name, cap in CAPS.items():
            size = os.path.getsize(os.path.join(AGENTS, name))
            self.assertLessEqual(size, cap, name)

    def test_gate_keywords_survive_the_split(self):
        text = "\n".join(agent_text(name) for name in CAPS)
        for word in KEYWORDS:
            self.assertTrue(word in text, f"keyword missing: {word}")

    def test_dev_batches_reads_and_runs_the_test_per_batch(self):
        text = read(os.path.join(AGENTS, "dev.md"))
        self.assertIn("in one Bash call", text)
        self.assertIn("once per edit batch", text)

    def test_reviewer_pass_report_is_counts_only(self):
        text = read(os.path.join(AGENTS, "reviewer.md"))
        self.assertIn("PASS: report only the verdict, the counts line", text)
        self.assertIn("FAIL: keep every wrong item with its quote", text)

    def test_architect_uses_batch(self):
        text = read(os.path.join(AGENTS, "architect.md"))
        self.assertIn("`cs-plan batch`", text)


if __name__ == "__main__":
    unittest.main()
