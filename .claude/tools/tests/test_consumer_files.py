"""Consumer files: what a consumer of ~/.claude needs stays tracked in git."""

import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CLAUDE = os.path.join(REPO, ".claude")
# Copied from scripts/install-claude-config.sh (CLAUDE_CONFIG_SKIP): these top-level items are not synced.
CLAUDE_CONFIG_SKIP = (".work", "logs", "parked", "projects", "bin-host", "settings.json")
# Dirs the installer syncs whole: each must hold at least one tracked file.
CONSUMER_DIRS = (".claude/agents", ".claude/tools", ".claude/practices", ".claude/bin")
AGENTS = (
    "arch-auditor.md",
    "architect.md",
    "docs-writer.md",
    "dev.md",
    "merger.md",
    "ops.md",
    "plan-reviewer.md",
    "publisher.md",
    "researcher.md",
    "reviewer.md",
    "scout.md",
    "sec-auditor.md",
    "tester.md",
    "ux-reviewer.md",
    "web-tester.md",
)
PRACTICES = (
    "audits.md",
    "bash.md",
    "branches.md",
    "code-nav.md",
    "coverage.md",
    "cpp.md",
    "gate-selection.md",
    "js.md",
    "localdev.md",
    "orch-pipeline.md",
    "pipeline-backlog.md",
    "pipeline-dev.md",
    "pipeline-graph.md",
    "plan-base-integrity.md",
    "plan-review.md",
    "planner-context.md",
    "planning.md",
    "python.md",
    "rust-wasm.md",
    "rust.md",
    "score.md",
)
PINNED = (
    *(f".claude/agents/{name}" for name in AGENTS),
    *(f".claude/practices/{name}" for name in PRACTICES),
    ".claude/tools/qrun.py",
    ".claude/tools/public_words_baseline.py",
    ".claude/settings.json",
)


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p]


def missing(tracked, required):
    """Required paths that no tracked file is (or sits under)."""
    found = []
    for req in required:
        if not any(p == req or p.startswith(req + "/") for p in tracked):
            found.append(req)
    return found


def synced_items(claude_dir, tracked):
    """Top-level items of .claude that scripts/install-claude-config.sh syncs.
    Names come from disk and from the index: an item on disk but untracked, or tracked but gone from disk,
    is listed."""
    names = set(os.listdir(claude_dir))
    names |= {p.split("/")[1] for p in tracked if p.startswith(".claude/") and p.count("/") >= 2}
    names |= {p[len(".claude/") :] for p in tracked if p.startswith(".claude/") and p.count("/") == 1}
    return [f".claude/{name}" for name in sorted(names) if name not in CLAUDE_CONFIG_SKIP]


class ConsumerFilesTest(unittest.TestCase):
    def setUp(self):
        self.tracked = tracked_files()

    def test_consumer_dirs_have_tracked_files(self):
        self.assertEqual(missing(self.tracked, CONSUMER_DIRS), [])

    def test_pinned_consumer_files_stay_tracked(self):
        self.assertEqual(missing(self.tracked, PINNED), [])

    def test_every_synced_top_level_item_is_tracked(self):
        self.assertEqual(missing(self.tracked, synced_items(CLAUDE, self.tracked)), [])

    def test_synced_item_on_disk_but_untracked_is_reported(self):
        # Mutation: index without paths.json while the disk lists it must report it missing.
        without = [p for p in self.tracked if p != ".claude/paths.json"]
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "paths.json"), "w").close()
            items = synced_items(tmp, without)
            self.assertEqual(missing(without, items), [".claude/paths.json"])

    def test_check_reports_untracked_pinned_file(self):
        # Mutation: a copy of the tracked list without dev.md must report it missing.
        without_dev = [p for p in self.tracked if p != ".claude/agents/dev.md"]
        self.assertEqual(missing(without_dev, [".claude/agents/dev.md"]), [".claude/agents/dev.md"])
