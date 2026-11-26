"""linkguard flags a `path:line` cite in a modified docs file whose quoted text is no longer on that line (a stale line number)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import linkguard

CODE = "import os\n\ndef f():\n    return 1\n"
DOC_OLD = "# notes\n\nold text\n"
DOC_NEW = (
    "# notes\n\n"
    'good: `src/mod.py:3` "def f():"\n'  # line 3 holds the quote: no note
    'stale: `src/mod.py:9` "def f():"\n'  # line 9 does not exist: note
    'moved: `src/mod.py:1` "def f():"\n'  # line 1 has no such text: note
    "plain: `src/mod.py:1` without a quote is not checked\n"
)


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


class CiteNotes(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        git(self.root, "init", "-q")
        os.makedirs(os.path.join(self.root, "src"))
        self.write("src/mod.py", CODE)
        self.write("docs/a.md", DOC_OLD)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_stale_cites_in_a_modified_doc_are_flagged(self):
        self.write("docs/a.md", DOC_NEW)
        notes = linkguard.cite_notes(self.root)
        self.assertEqual(len(notes), 2)
        self.assertIn("docs/a.md cites src/mod.py:9", notes[0])
        self.assertIn("docs/a.md cites src/mod.py:1", notes[1])
        self.assertFalse(any(":3" in n for n in notes))

    def test_committed_doc_is_not_checked(self):
        self.write("docs/b.md", DOC_NEW)  # new file: checked
        notes = linkguard.cite_notes(self.root)
        self.assertEqual(len(notes), 2)
        self.assertTrue(all("docs/b.md" in n for n in notes))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "docs")
        self.assertEqual(linkguard.cite_notes(self.root), [])

    def test_quoted_text_on_the_cited_line_passes(self):
        self.write("docs/a.md", 'see `src/mod.py:4` "    return 1"\n')
        self.assertEqual(linkguard.cite_notes(self.root), [])

    def test_check_appends_cite_notes_to_the_dev_note(self):
        self.write("docs/a.md", DOC_NEW)
        with (
            mock.patch.object(linkguard, "_linked", return_value=(self.root, [])),
            mock.patch.object(linkguard, "_base", return_value={}),
            mock.patch.object(linkguard, "_save"),
        ):
            note = linkguard.check(self.root, "s", "task-1", "add")
        self.assertIn("docs cite out of date", note)


if __name__ == "__main__":
    unittest.main()
