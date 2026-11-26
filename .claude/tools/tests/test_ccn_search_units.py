import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import lp_share
from tests.support.tmpdirs import tracked_mkdtemp

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SearchUnits(unittest.TestCase):
    def test_bash_kinds(self):
        k = lp_share._bash_kind
        self.assertEqual(k("lp nav def a.py f"), "local:nav-def")
        self.assertEqual(k("lp find 'x'"), "local:find")
        self.assertEqual(k("cd src && grep -rn foo ."), "grep")
        self.assertEqual(k("FOO=1 rg foo src"), "grep")
        self.assertIsNone(k("cat x | grep foo"))
        self.assertIsNone(k("grep foo README.md"))
        self.assertIsNone(k("grep foo /root/.claude/tools"))
        self.assertIsNone(k("cat <<EOF\ngrep foo\nEOF"))

    def test_classify(self):
        self.assertEqual(lp_share.classify("Grep", {"pattern": "x", "path": "src"}), "grep")
        self.assertIsNone(lp_share.classify("Grep", {"pattern": "x", "path": "notes.md"}))
        self.assertEqual(lp_share.classify("Glob", {"pattern": "src/*.py"}), "glob")
        self.assertIsNone(lp_share.classify("Glob", {"pattern": "*.md"}))
        self.assertIsNone(lp_share.classify("Grep", {"path": "/root/.claude/x"}))
        self.assertEqual(lp_share.classify("Bash", {"command": "grep -r a ."}), "grep")
        self.assertIsNone(lp_share.classify("Read", {"file_path": "a"}))
        self.assertEqual(lp_share.classify("Grep", None), "grep")

    def _transcript(self):
        d = tracked_mkdtemp()
        p = os.path.join(d, "s.jsonl")

        def use(n, i):
            return {"message": {"content": [{"type": "tool_use", "name": n, "input": i}, "junk"]}}

        rows = [
            use("Bash", {"command": "lp find x"}),
            use("Bash", {"command": "grep -r a ."}),
            use("Grep", {"pattern": "q"}),
            {"message": {"content": "text"}},
            {"message": None},
        ]
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        return p

    def test_count_file_and_share(self):
        c = lp_share.count_file(self._transcript())
        self.assertEqual(dict(c), {"local:find": 1, "grep": 2})
        self.assertAlmostEqual(lp_share.share(c), 1 / 3)
        self.assertEqual(lp_share.share(lp_share.count_file("/nonexistent/x.jsonl")), 0.0)

    def test_cli_prints_counts_and_share(self):
        p = self._transcript()
        r = subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "pipeline", "lp_share.py"), p], check=False, capture_output=True, text=True, cwd=TOOLS)
        self.assertIn("local.py share: 33%", r.stdout)
        self.assertIn("'grep': 2", r.stdout)


if __name__ == "__main__":
    unittest.main()
