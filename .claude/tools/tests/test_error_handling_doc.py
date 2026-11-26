"""docs/error-handling.md: each kept swallow row points at a real `except` line, and every `# KEEP:` in the code has a row."""

import os
import re
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DOC = os.path.join(ROOT, "docs", "error-handling.md")
TOOLS = os.path.join(ROOT, ".claude", "tools")
HEADINGS = ["## search", "## localmodel", "## audit", "## top-level"]
ROW_RE = re.compile(r"^- `([^`]+):(\d+)` - (.+)$", re.M)
KEEP_RE = re.compile(r"#\s*KEEP:")
EXCEPT_RE = re.compile(r"\bexcept\b")


def doc_problems(doc_text, root):
    """Return a list of problems: missing headings, rows whose file or line is wrong, rows without a reason."""
    problems = [f"missing heading {h}" for h in HEADINGS if h not in doc_text.splitlines()]
    for path, num, reason in ROW_RE.findall(doc_text):
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            problems.append(f"{path}:{num} no such file")
            continue
        with open(full, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        n = int(num)
        if not 1 <= n <= len(lines):
            problems.append(f"{path}:{n} past end of file")
        elif not EXCEPT_RE.search(lines[n - 1]):
            problems.append(f"{path}:{n} is not an except line")
        if not reason.strip():
            problems.append(f"{path}:{n} empty reason")
    return problems


def code_keep_sites(tools):
    """Return the set of `file:line` for each `# KEEP:` comment under the tools folder, tests excluded."""
    sites = set()
    for dirpath, dirnames, filenames in os.walk(tools):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "__pycache__")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            with open(full, encoding="utf-8") as fh:
                for num, line in enumerate(fh, 1):
                    if KEEP_RE.search(line):
                        sites.add(f"{os.path.relpath(full, ROOT)}:{num}")
    return sites


class ErrorHandlingDocTest(unittest.TestCase):
    def setUp(self):
        with open(DOC, encoding="utf-8") as fh:
            self.doc = fh.read()

    def test_doc_rows_point_at_except_lines(self):
        self.assertEqual(doc_problems(self.doc, ROOT), [])

    def test_every_keep_comment_has_a_row(self):
        rows = {f"{p}:{n}" for p, n, _ in ROW_RE.findall(self.doc)}
        self.assertEqual(code_keep_sites(TOOLS), rows)

    def test_decoy_row_on_non_except_line_fails(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        with open(os.path.join(td.name, "mod.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\ntry:\n    pass\nexcept OSError:\n    pass\n")
        doc = "## search\n- `mod.py:1` - decoy\n- `mod.py:4` - real\n## localmodel\n## audit\n## top-level\n"
        problems = doc_problems(doc, td.name)
        self.assertEqual(problems, ["mod.py:1 is not an except line"])


if __name__ == "__main__":
    unittest.main()
