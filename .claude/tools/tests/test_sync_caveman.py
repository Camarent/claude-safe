"""sync_caveman.py in a scratch tree (it works relative to its own location): inlines the skill body into agents, idempotent, skips the text-shipping agents."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sync_caveman.py"
SKILL = (
    "---\nname: caveman\n---\n\n# caveman\n\n## Persistence\nchat only\n\n## Rules\n### 1. Answer first\nAnswer, then reason.\n\n## Pre-send check\nchat only\n"
)


class SyncCavemanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "tools").mkdir()
        shutil.copy(TOOL, self.root / "tools" / "sync_caveman.py")
        (self.root / "agents").mkdir()
        (self.root / "skills" / "caveman").mkdir(parents=True)
        (self.root / "skills" / "caveman" / "SKILL.md").write_text(SKILL, encoding="utf-8")
        (self.root / "agents" / "dev.md").write_text("---\nname: dev\nskills:\n  - caveman\n---\nBody\n", encoding="utf-8")
        (self.root / "agents" / "publisher.md").write_text("---\nname: publisher\n---\nBody\n", encoding="utf-8")

    def sync(self, *args):
        return subprocess.run([sys.executable, "-I", str(self.root / "tools" / "sync_caveman.py"), *args], capture_output=True, text=True, check=True).stdout

    def test_check_flags_stale_agents_and_writes_nothing(self):
        dev = self.root / "agents" / "dev.md"
        before = dev.read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(self.root / "tools" / "sync_caveman.py"), "--check"], capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, dev.read_text(encoding="utf-8")), (1, before))
        self.assertIn("dev", r.stdout)
        self.sync()
        self.assertEqual(self.sync("--check"), "")

    def test_inlines_the_rules_without_chat_only_sections(self):
        self.assertIn("dev ", self.sync())
        dev = (self.root / "agents" / "dev.md").read_text(encoding="utf-8")
        self.assertIn("<!-- caveman:begin -->", dev)
        self.assertIn("Answer, then reason.", dev)
        self.assertNotIn("chat only", dev)
        self.assertNotIn("skills:\n  - caveman", dev)
        self.assertNotIn("caveman:begin", (self.root / "agents" / "publisher.md").read_text(encoding="utf-8"))

    def test_second_run_changes_nothing(self):
        self.sync()
        first = (self.root / "agents" / "dev.md").read_text(encoding="utf-8")
        self.sync()
        self.assertEqual((self.root / "agents" / "dev.md").read_text(encoding="utf-8"), first)
        self.assertEqual(first.count("caveman:begin"), 1)
        self.assertEqual([p.name for p in (self.root / "agents").iterdir() if p.name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
