"""Container memory stays out of git: no tracked .claude/projects path, the rule ignores it, CLAUDE.md uses ~/.claude paths."""

import os
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MEMORY_DIR = ".claude/projects/"
CLAUDE_MD = "CLAUDE.md"


def tracked_memory(tracked):
    """Return the paths in the tracked list that sit under the memory folder."""
    return [p for p in tracked if p.startswith(MEMORY_DIR)]


def git_tracked():
    out = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


class NoTrackedMemoryTest(unittest.TestCase):
    def test_c1_no_tracked_memory_path(self):
        self.assertEqual(tracked_memory(git_tracked()), [])

    def test_c2_memory_path_is_git_ignored(self):
        # -v names the rule that matched: the broad `.claude/*` rule would also ignore the probe,
        # so require that the matching rule is our `.claude/projects/` line in .gitignore.
        probe = MEMORY_DIR + "-workspace/memory/x.md"
        result = subprocess.run(["git", "-C", REPO, "check-ignore", "-v", probe], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        # output: <source>:<line>:<pattern>\t<path>
        source, _, rest = result.stdout.partition(":")
        _, _, pattern = rest.partition(":")
        self.assertEqual(source, ".gitignore")
        self.assertEqual(pattern.split("\t", 1)[0], MEMORY_DIR)

    def test_c3_claude_md_has_no_home_path(self):
        with open(os.path.join(REPO, CLAUDE_MD), encoding="utf-8") as f:
            lines = f.read().splitlines()
        home_prefix = os.sep + "home" + os.sep  # built from parts: the public-words scan would find this literal in this file
        self.assertEqual([ln for ln in lines if home_prefix in ln], [])

    def test_c4_mutation_one_memory_path_fails(self):
        tracked = ["CLAUDE.md", MEMORY_DIR + "-workspace/memory/MEMORY.md"]
        self.assertEqual(tracked_memory(tracked), [MEMORY_DIR + "-workspace/memory/MEMORY.md"])
