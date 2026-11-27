"""Agent files stay under their recorded size caps; gate-rule keywords survive the split into <agent>/rare-rules.md."""

import glob
import os
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agents")

# Recorded caps in bytes: the split files hold the rarely used rules, the main files stay small.
# architect.md: 27000 (was 26000) because task-8 moved four always-read brief rules into it; they must sit in the main file, not rare-rules.md.
# dev.md: 13700 (was 13500) and reviewer.md: 12300 (was 12200) because task-3 added the Bash rules line to both (and the CCN and tooling-BLOCKED lines to dev.md); they must sit in the main files, not rare-rules.md.
# dev.md: 14600 (was 13700) and reviewer.md: 13300 (was 12300) because task-8 put the E1-E4 escalation rules in both main files: test_escalation_docs.py requires them word for word in each doc, so they cannot move to rare-rules.md.
# dev.md: 15100 (was 14600) and reviewer.md: 13700 (was 13300) because the five denial brief rules (cd_git, qrun, segments, sed, guard) sit in both main files: test_escalation_docs.py requires them word for word in each doc, so they cannot move to rare-rules.md.
# architect.md: 27400 (was 27000) because the UI job rule bullet (glossary, sign-off, prototype) is always-read planning guidance; it sits in the main file, not rare-rules.md.
# architect.md: 27700 (was 27400) because the three plan-check rules (qrun-op, pins, docs-count) are always-read planning guidance; they sit in the main file, not rare-rules.md.
# architect.md: 28000 (was 27700) because task-8 replaced the .work output bullet with the output-path rule and added the research and ops-input bullets; they are always-read planning guidance, so they sit in the main file, not rare-rules.md.
# architect.md: 28600 (was 28000) because task-10 added the research trigger table; always-read planning guidance, so it sits in the main file, not rare-rules.md.
# architect.md: 28800 (was 28600) because the wiring bullet (every new module mounted from the real entry, reach proof) is always-read planning guidance; it sits in the main file, not rare-rules.md.
CAPS = {"architect.md": 28800, "dev.md": 15100, "reviewer.md": 13700}

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

    def test_architect_research_and_ops_input_rules(self):
        text = read(os.path.join(AGENTS, "architect.md"))
        self.assertNotIn("absolute main-tree paths", text)
        self.assertIn("Research task (`Type: research`", text)
        self.assertIn("the driver blocks once up front", text)

    def test_merger_auto_merges_only_init_add_add_conflicts(self):
        text = read(os.path.join(AGENTS, "merger.md"))
        self.assertIn("an add/add conflict on an `__init__.py` (both branches added the file) keeps both sides' lines", text)
        self.assertIn("any other add/add conflict stays a CONFLICT for the lead", text)
        self.assertIn("keeps both sides' lines, each line once, base side first", text)
        self.assertNotIn("any file", text)
        self.assertIn("reports CONFLICT except add/add on `__init__.py`, merged per step 4", text)
        self.assertIn("Edit and resolve nothing except step 4's `__init__.py` case", text)


class FreshnessAuditorAgent(unittest.TestCase):
    def test_freshness_auditor_is_read_only_haiku(self):
        text = read(os.path.join(AGENTS, "freshness-auditor.md"))
        front = text.split("---", 2)[1]
        self.assertIn("model: haiku", front)
        self.assertIn("effort: low", front)
        tools = next(ln for ln in front.splitlines() if ln.startswith("tools:"))
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertIn("VERDICT: CLEAN", text)
        self.assertIn("VERDICT: FIXES", text)


if __name__ == "__main__":
    unittest.main()
