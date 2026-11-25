import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import retro_lessons_check as rlc
from tests.support.tmpdirs import tracked_mkdtemp

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs" / "retro-lessons.md"
STUCK_HEAD = "## Stuck causes\n\n| stage | task | report | cause |\n|---|---|---|---|\n"
USELESS_HEAD = "## Useless escalations\n\n| stage | task | report | cause |\n|---|---|---|---|\n"


def row(report, cause="cause"):
    return f"| pipeline-fixes | task-1 | {report} | {cause} |\n"


def make_doc(d, stuck, useless, totals=(1, 1)):
    """Write a retro lessons report into D with the given row lines; -> its path."""
    text = (
        "# Retro lessons\n\nValidator paragraph.\n\n"
        + STUCK_HEAD
        + "".join(stuck)
        + "\n"
        + USELESS_HEAD
        + "".join(useless)
        + "\n"
        + f"Totals: stuck causes {totals[0]}, useless escalations {totals[1]}.\n"
    )
    path = os.path.join(d, "retro-lessons.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def make_report(d, name="report-a1.md"):
    """Create an existing report file in D; -> its path."""
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("report\n")
    return path


class RetroLessonsCheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = tracked_mkdtemp()
        self.report = make_report(self.dir)

    def run_check(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rlc.main([path])
        return code, out.getvalue()

    def test_good_fixture_passes(self):
        path = make_doc(self.dir, [row(self.report)], [row(self.report)], totals=(1, 1))
        code, out = self.run_check(path)
        self.assertEqual(code, 0, out)
        self.assertIn("OK:", out)

    def test_missing_report_file_fails(self):
        path = make_doc(self.dir, [row(os.path.join(self.dir, "gone.md"))], [row(self.report)])
        code, out = self.run_check(path)
        self.assertEqual(code, 1)
        self.assertIn("report file missing", out)

    def test_missing_section_fails(self):
        text = "# Retro lessons\n\n" + STUCK_HEAD + row(self.report) + "\nTotals: stuck causes 1, useless escalations 0.\n"
        path = os.path.join(self.dir, "no-section.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        code, out = self.run_check(path)
        self.assertEqual(code, 1)
        self.assertIn("missing section: ## Useless escalations", out)

    def test_totals_mismatch_fails(self):
        path = make_doc(self.dir, [row(self.report)], [row(self.report)], totals=(2, 1))
        code, out = self.run_check(path)
        self.assertEqual(code, 1)
        self.assertIn("do not match rows", out)

    def test_deleted_row_breaks_totals(self):
        # mutation: the good fixture has two stuck rows; delete one, the footer still says 2
        good = make_doc(self.dir, [row(self.report), row(self.report, "second")], [row(self.report)], totals=(2, 1))
        code, _ = self.run_check(good)
        self.assertEqual(code, 0)
        with open(good, encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        mutated = [ln for ln in lines if not ln.endswith("| second |\n")]
        path = os.path.join(self.dir, "mutated.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(mutated)
        code, out = self.run_check(path)
        self.assertEqual(code, 1)
        self.assertIn("do not match rows 1, 1", out)

    def test_real_doc_passes(self):
        code, out = self.run_check(str(DOC))
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
