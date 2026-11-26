"""Cite check: `path:line` cites in added doc and comment lines, and the dev report text, must point at real lines and symbols."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import cite_check

DIFF = """diff --git a/docs/core.md b/docs/core.md
--- a/docs/core.md
+++ b/docs/core.md
@@ -0,0 +1,4 @@
+```
+see progress.py:82
+```
+plain progress.py:82
diff --git a/pipeline/x.py b/pipeline/x.py
--- a/pipeline/x.py
+++ b/pipeline/x.py
@@ -0,0 +1,2 @@
+y = 1
+z = 2  # see progress.py:82
"""


def progress_py(def_line=136):
    """progress.py with `save_progress` defined at def_line (filler comments before it)."""
    return "\n".join(["# filler"] * (def_line - 1) + ["def save_progress(state):", "    pass"]) + "\n"


class CiteCheck(unittest.TestCase):
    def tree(self, files):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for name, text in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text, encoding="utf-8")
        return str(d)

    def test_wrong_cite_names_real_place(self):
        wt = self.tree({"progress.py": progress_py(136)})
        added = [("core.md", 153, "`save_progress` (progress.py:82) runs the save")]
        self.assertEqual(cite_check.check(wt, added, ""), ["core.md:153 cites progress.py:82 but save_progress is at 136"])

    def test_correct_cite_no_message(self):
        wt = self.tree({"progress.py": progress_py(136)})
        added = [("core.md", 153, "`save_progress` (progress.py:136) runs the save")]
        self.assertEqual(cite_check.check(wt, added, ""), [])

    def test_symbol_outside_window_is_flagged(self):
        # the symbol sits 10 lines from the cite: outside the window of 3 (fixed here, not read from the constant), so flagged
        wt = self.tree({"progress.py": progress_py(92)})
        added = [("core.md", 5, "`save_progress` (progress.py:82)")]
        self.assertEqual(cite_check.check(wt, added, ""), ["core.md:5 cites progress.py:82 but save_progress is at 92"])

    def test_symbol_not_found(self):
        wt = self.tree({"progress.py": progress_py(136)})
        added = [("core.md", 7, "`ghost` (progress.py:82)")]
        self.assertEqual(cite_check.check(wt, added, ""), ["core.md:7 cites progress.py:82: symbol not found in progress.py"])

    def test_fenced_block_ignored(self):
        self.assertEqual(
            [(f, ln) for f, ln, _ in cite_check.added_lines(DIFF)],
            [("docs/core.md", 1), ("docs/core.md", 2), ("docs/core.md", 3), ("docs/core.md", 4), ("pipeline/x.py", 2)],
        )
        # the fenced block is already in the file (not added by this diff): its added row is ignored, the prose row after it is checked
        wt = self.tree(
            {"progress.py": progress_py(136), "docs/core.md": "```\n`save_progress` (progress.py:82)\n```\nplain `save_progress` (progress.py:82)\n"}
        )
        added = [("docs/core.md", 2, "`save_progress` (progress.py:82)"), ("docs/core.md", 4, "plain `save_progress` (progress.py:82)")]
        self.assertEqual(cite_check.check(wt, added, ""), ["docs/core.md:4 cites progress.py:82 but save_progress is at 136"])
        self.assertEqual(cite_check.check(wt, [], "```\n`save_progress` (progress.py:82)\n```\n"), [])

    def test_cite_outside_worktree_not_read(self):
        base = self.tree({"secret.py": "# x\n", "wt/keep.py": ""})
        wt = os.path.join(base, "wt")
        self.assertEqual(
            cite_check.check(wt, [("core.md", 1, "see ../secret.py:1")], ""),
            ["core.md:1 cites ../secret.py:1 but ../secret.py is outside the worktree"],
        )

    def test_url_ignored(self):
        self.assertEqual(cite_check.cites("see https://host:80/x now"), [])
        wt = self.tree({"progress.py": progress_py(136)})
        self.assertEqual(cite_check.check(wt, [("core.md", 1, "see https://host:80/x")], ""), [])

    def test_comment_tail_only(self):
        self.assertEqual(cite_check.added_lines(DIFF)[-1], ("pipeline/x.py", 2, "see progress.py:82"))

    def test_line_beyond_eof(self):
        wt = self.tree({"progress.py": progress_py(136)})
        added = [("core.md", 9, "see progress.py:500")]
        self.assertEqual(cite_check.check(wt, added, ""), ["core.md:9 cites progress.py:500 but progress.py has 137 lines"])

    def test_missing_file(self):
        wt = self.tree({})
        self.assertEqual(cite_check.check(wt, [("core.md", 3, "see nope.py:4")], ""), ["core.md:3 cites nope.py:4 but nope.py is missing"])

    def test_bare_name_resolves_to_unique_file(self):
        wt = self.tree({"scripts/smoke/progress.py": progress_py(136)})
        added = [("core.md", 9, "see progress.py:100")]
        self.assertEqual(cite_check.check(wt, added, ""), [])

    def test_bare_name_with_two_files_stays_missing(self):
        wt = self.tree({"a/progress.py": progress_py(136), "b/progress.py": progress_py(136)})
        added = [("core.md", 9, "see progress.py:100")]
        self.assertEqual(cite_check.check(wt, added, ""), ["core.md:9 cites progress.py:100 but progress.py is missing"])

    def test_report_text_cites_checked(self):
        wt = self.tree({"progress.py": progress_py(136)})
        self.assertEqual(
            cite_check.check(wt, [], "ok\n`save_progress` (progress.py:82)\n"),
            ["report:2 cites progress.py:82 but save_progress is at 136"],
        )

    def test_range_end_checked(self):
        wt = self.tree({"progress.py": progress_py(136)})
        self.assertEqual(
            cite_check.check(wt, [("core.md", 1, "see progress.py:130-200")], ""),
            ["core.md:1 cites progress.py:130-200 but progress.py has 137 lines"],
        )

    def test_bad_cite_quoted_as_fixed_skipped(self):
        # the report quotes the bad cite (line 900 of a.py) as already fixed: skipped; the fixed cite (line 12) is still checked
        wt = self.tree({"a.py": "x = 1\n" * 5})
        self.assertEqual(
            cite_check.check(wt, [], "the cite `a.py:900` was wrong, fixed to a.py:12\n"),
            ["report:1 cites a.py:12 but a.py has 5 lines"],
        )

    def test_bad_cite_quoted_without_fixed_word_still_flagged(self):
        wt = self.tree({"a.py": "x = 1\n" * 5})
        self.assertEqual(
            cite_check.check(wt, [], "the cite `a.py:900` is wrong\n"),
            ["report:1 cites a.py:900 but a.py has 5 lines"],
        )

    def test_bad_cite_unquoted_next_to_fixed_word_still_flagged(self):
        wt = self.tree({"a.py": "x = 1\n" * 5})
        self.assertEqual(
            cite_check.check(wt, [], "the cite a.py:900 was wrong\n"),
            ["report:1 cites a.py:900 but a.py has 5 lines"],
        )

    def test_split_files_deleted_renamed_and_big_removal(self):
        diff = (
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
            "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
            "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1,40 +1 @@\n" + "-x\n" * 40 + "+y = 1\n"
            "diff --git a/small.py b/small.py\n--- a/small.py\n+++ b/small.py\n@@ -1,39 +1 @@\n" + "-x\n" * 39 + "+y = 1\n"
        )
        self.assertEqual(cite_check.split_files(diff), {"gone.py", "old.py", "new.py", "big.py"})

    def test_split_cite_in_report_skips_line_and_symbol_tests(self):
        wt = self.tree({"big.py": "x = 1\n" * 5, "progress.py": progress_py(136)})
        report = "the cite big.py:900 moved\n`save_progress` (progress.py:82) moved\n"
        self.assertEqual(cite_check.check(wt, [], report, split={"big.py"}), ["report:2 cites progress.py:82 but save_progress is at 136"])
        self.assertEqual(
            cite_check.check(wt, [], report), ["report:1 cites big.py:900 but big.py has 5 lines", "report:2 cites progress.py:82 but save_progress is at 136"]
        )

    def test_split_cite_missing_file_still_fails(self):
        wt = self.tree({})
        self.assertEqual(cite_check.check(wt, [], "gone.py:3\n", split={"gone.py"}), ["report:1 cites gone.py:3 but gone.py is missing"])

    def test_split_cite_in_added_doc_line_still_fully_checked(self):
        wt = self.tree({"big.py": "x = 1\n" * 5})
        self.assertEqual(cite_check.check(wt, [("core.md", 2, "see big.py:900")], "", split={"big.py"}), ["core.md:2 cites big.py:900 but big.py has 5 lines"])

    def test_bare_names_flags_only_paths_without_directory(self):
        self.assertTrue(cite_check.bare_names(["core.md:1 cites progress.py:82 but x"]))
        self.assertFalse(cite_check.bare_names(["core.md:1 cites pipeline/progress.py:82 but x"]))


if __name__ == "__main__":
    unittest.main()
