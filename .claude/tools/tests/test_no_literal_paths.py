"""Lint: agent, command and skill texts name tools by cs-* command (paths.json), not by container path.

Scope: .claude/agents, .claude/commands, .claude/skills. Practices, CLAUDE.md and ORCHESTRATOR.md are owned by another agent.
Every hit is reported as file:line.
"""

import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, "..", ".."))  # .claude/
SCOPE = ("agents", "commands", "skills")
HOME_LITERAL = "/root/.claude/"
TOOL_LITERAL = re.compile(r"/root/\.claude/(tools|bin)/")
WORKSPACE_LITERAL = re.compile(r"/workspace(?![\w-])")
HOME_PATH = re.compile(r"/root/\.claude/[A-Za-z0-9_./*-]+")


def scoped_files():
    out = []
    for sub in SCOPE:
        for root, _dirs, files in os.walk(os.path.join(CLAUDE, sub)):
            out += [os.path.join(root, f) for f in files if f.endswith(".md")]
    return sorted(out)


def hits(pattern, files):
    out = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if pattern.search(line):
                    out.append(f"{os.path.relpath(path, CLAUDE)}:{n}: {line.strip()[:120]}")
    return out


def dangling_homes(files):
    """Home paths that do not exist under .claude/ (a `*` glob or `<placeholder>` text is matched loosely)."""
    out = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                for m in HOME_PATH.finditer(line):
                    raw = m.group(0).rstrip(".")
                    rel = raw[len(HOME_LITERAL) :]  # text cut at a `<placeholder>` leaves its directory prefix: checked as is
                    if not glob.glob(os.path.join(CLAUDE, rel)):
                        out.append(f"{os.path.relpath(path, CLAUDE)}:{n}: {raw} does not exist under .claude/")
    return out


class NoLiteralPathsTest(unittest.TestCase):
    def setUp(self):
        self.files = scoped_files()

    def test_scope_is_not_empty(self):
        self.assertGreater(len(self.files), 10)

    def test_no_tool_or_bin_container_path(self):
        self.assertEqual(hits(TOOL_LITERAL, self.files), [], "use the cs-* command name (paths.json)")

    def test_no_workspace_literal(self):
        self.assertEqual(hits(WORKSPACE_LITERAL, self.files), [], "say 'project root' (the working directory), not /workspace")

    def test_home_paths_exist(self):
        self.assertEqual(dangling_homes(self.files), [])


if __name__ == "__main__":
    unittest.main()
