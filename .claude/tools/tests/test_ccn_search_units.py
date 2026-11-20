import json
import os
import subprocess
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import ccn, lp_share
from tests.support.tmpdirs import tracked_mkdtemp

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CcnUnits(unittest.TestCase):
    def test_git_failure_gives_empty_output(self):
        with mock.patch.object(ccn.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="x")):
            self.assertEqual(ccn._git("w", "diff"), "")

    def test_changed_lines_parses_hunks_and_skips_deleted_files(self):
        diff = "+++ b/a.py\n@@ -1,2 +5,3 @@\n@@ -1 +2,0 @@\n+++ /dev/null\n@@ -1 +9 @@\n"
        with mock.patch.object(ccn, "_git", return_value=diff):
            self.assertEqual(ccn.changed_lines("w", "m", "j"), {"a.py": {5, 6, 7, 2}})
        with mock.patch.object(ccn, "_git", return_value="+++ /dev/null\n@@ -1 +9 @@\n"):
            self.assertEqual(ccn.changed_lines("w", "m", "j"), {})

    def test_worst_changed_picks_highest_in_changed_range(self):
        csv_out = (
            "1,5,0,0,0,0,./a.py,f,f,3,6\n"
            "1,12,0,0,0,0,./a.py,g,g,3,6\n"  # high but changed lines 5..: in range
            "1,30,0,0,0,0,./a.py,h,h,50,60\n"  # high but not touched
            "1,40,0,0,0,0,./b.py,i,i,1,9\n"  # file not changed
            "bad,row\n"
            "1,x,0,0,0,0,./a.py,j,j,1,9\n"
            "1,3,0,0,0,0,./a.py,k,k,3,6\n"
        )
        with (
            mock.patch.object(ccn, "changed_lines", return_value={"a.py": {5}}),
            mock.patch.object(ccn.subprocess, "run", return_value=mock.Mock(stdout=csv_out)),
        ):
            self.assertEqual(ccn.worst_changed("w", "m", "j", 10), (12, "g", "a.py"))
            self.assertIsNone(ccn.worst_changed("w", "m", "j", 99))

    def test_worst_changed_no_lines_is_none(self):
        with mock.patch.object(ccn, "changed_lines", return_value={}):
            self.assertIsNone(ccn.worst_changed("w", "m", "j", 10))

    def _hit(self, worst, th=None):
        from pipeline import worktrees

        d = tracked_mkdtemp()
        with open(os.path.join(d, "pipeline.json"), "w") as f:
            json.dump({"review_ccn": th} if th else {}, f)
        a = mock.Mock(dir=d, stage="s", feature=None)
        fake_wt = mock.Mock()
        fake_wt._parts.return_value = ({}, "job", [("", "r", "w")])
        fake_wt._base_branch.return_value = "main"
        with (
            mock.patch.object(worktrees, "job_info", return_value={"x": 1}),
            mock.patch.object(worktrees, "wt", fake_wt),
            mock.patch.object(ccn, "worst_changed", return_value=worst) as w,
        ):
            return ccn.review_ccn_hit(a, "t1"), w

    def test_review_hit_reason_and_threshold(self):
        r, w = self._hit((14, "f", "m.py"), th=7)
        self.assertEqual(r, "reviewer on Sonnet: ccn 14 in f (m.py), above review_ccn 7")
        self.assertEqual(w.call_args[0][3], 7)

    def test_review_no_hit_uses_default_threshold(self):
        r, w = self._hit(None)
        self.assertIsNone(r)
        self.assertEqual(w.call_args[0][3], ccn.DEFAULT_REVIEW_CCN)


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
