"""audit/swallow_scan.py: swallow and broad-except rules on a fixture tree, the decoys that are not listed, and --defer marking."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))

from audit import swallow_scan  # noqa: E402  (.claude/tools is put on sys.path above)

FIXTURE_FILES = {
    "search/handlers.py": """\
def bare_pass():
    try:
        g()
    except OSError:
        pass


def loop_continue():
    for x in y:
        try:
            g(x)
        except ValueError:
            continue


def return_none():
    try:
        g()
    except KeyError:
        return None


def broad_return_call():
    try:
        g()
    except Exception:
        return compute()


def decoy_reraise():
    try:
        g()
    except OSError:
        raise RuntimeError("x")


def decoy_swallowed_call():
    try:
        g()
    except OSError as e:
        swallowed("where", e)


def decoy_log_only():
    try:
        g()
    except KeyError:
        log.info("missing")
""",
    "top.py": """\
def f():
    try:
        g()
    except:
        pass


def reraise_all():
    try:
        g()
    except BaseException:
        raise
""",
    "tests/skipped.py": """\
def f():
    try:
        g()
    except OSError:
        pass
""",
}


def _write_tree(root, files):
    for rel, src in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)


class ScanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        _write_tree(self.root, FIXTURE_FILES)
        self.sites = swallow_scan.scan_tree(self.root)

    def kinds_at(self, rel):
        return {s.line: (s.kind, s.exc, s.package) for s in self.sites if s.path == rel}

    def test_swallow_rule_lists_pass_continue_and_return_none(self):
        found = self.kinds_at(os.path.join("search", "handlers.py"))
        self.assertEqual(found[4], ("swallow", "OSError", "search"))
        self.assertEqual(found[12], ("swallow", "ValueError", "search"))
        self.assertEqual(found[19], ("swallow", "KeyError", "search"))

    def test_broad_rule_lists_non_const_return(self):
        found = self.kinds_at(os.path.join("search", "handlers.py"))
        self.assertEqual(found[26], ("broad", "Exception", "search"))

    def test_decoys_are_not_listed(self):
        found = self.kinds_at(os.path.join("search", "handlers.py"))
        for line in (33, 40, 47):  # re-raise, swallowed() call, log-only: all narrow types
            self.assertNotIn(line, found)

    def test_bare_except_pass_is_swallow_and_reraise_all_is_not_listed(self):
        found = self.kinds_at("top.py")
        self.assertEqual(found, {4: ("swallow", swallow_scan.BARE, swallow_scan.TOP_LEVEL)})

    def test_tests_folder_is_skipped(self):
        self.assertEqual(self.kinds_at(os.path.join("tests", "skipped.py")), {})

    def test_render_defer_marks_only_that_file_and_leaves_decision_blank(self):
        deferred = os.path.join(self.root, "top.py")
        text = swallow_scan.render(self.sites, self.root, defer=[deferred])
        rows = [ln for ln in text.splitlines() if ln.startswith("| top.py:") or ln.startswith("| search/handlers.py:")]
        top_rows = [ln for ln in rows if ln.startswith("| top.py:")]
        self.assertEqual(len(top_rows), 1)
        self.assertIn(swallow_scan.DEFER_NOTE, top_rows[0])
        self.assertTrue(top_rows[0].endswith("| |"))
        for ln in rows:
            if ln.startswith("| search/handlers.py:"):
                self.assertNotIn(swallow_scan.DEFER_NOTE, ln)

    def test_counts_by_package(self):
        text = swallow_scan.render(self.sites, self.root)
        self.assertIn("| search | 3 | 1 | 4 |", text)
        self.assertIn("| top-level | 1 | 0 | 1 |", text)


if __name__ == "__main__":
    unittest.main()
