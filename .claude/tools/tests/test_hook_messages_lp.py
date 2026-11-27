"""Agents name `lp` before they search; the lead rules say the same for the lead session."""

import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.join(HERE, "..", "..", "agents")
LEAD_RULES = os.path.join(HERE, "..", "..", "practices", "lead-rules.md")

# Agents that search the code: each must name `lp` before its first search instruction.
SEARCHING_AGENTS = ("dev.md", "reviewer.md", "scout.md", "arch-auditor.md", "sec-auditor.md", "architect.md")
NAV_HEADER = "Code navigation:"
LP_MARKERS = ("lp find", "lp nav")
SEARCH_WORDS = ("Grep", "grep", "Glob")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def body_lines(text):
    """Lines after the YAML frontmatter (the second `---` line), so the tools line does not count."""
    lines = text.splitlines()
    if lines and lines[0] == "---":
        end = lines.index("---", 1)
        return lines[end + 1 :]
    return lines


def nav_line_index(lines):
    """Index of the first `Code navigation:` line, the one that must name lp; -1 when absent."""
    for i, line in enumerate(lines):
        if line.startswith(NAV_HEADER):
            return i
    return -1


def first_search_index(lines):
    for i, line in enumerate(lines):
        if any(word in line for word in SEARCH_WORDS):
            return i
    return -1


class LpBeforeSearch(unittest.TestCase):
    def test_each_searching_agent_names_lp_before_its_first_search(self):
        for name in SEARCHING_AGENTS:
            with self.subTest(agent=name):
                lines = body_lines(read(os.path.join(AGENTS, name)))
                nav_at = nav_line_index(lines)
                self.assertGreaterEqual(nav_at, 0, f"{name}: no `Code navigation:` line")
                self.assertTrue(any(marker in lines[nav_at] for marker in LP_MARKERS), f"{name}: navigation line does not name lp")
                search_at = first_search_index(lines)
                if search_at >= 0:
                    self.assertLessEqual(nav_at, search_at, f"{name}: search instruction at body line {search_at + 1} comes before lp")

    def test_lead_rules_name_lp_before_grep_in_the_lead_session(self):
        text = read(LEAD_RULES)
        self.assertIn("lp nav", text)
        self.assertIn("before Grep, Glob or grep through Bash", text)
        self.assertIn("never chain lp after cd or ';' (one call each)", text)


if __name__ == "__main__":
    unittest.main()
