"""Characterization tests for plan_context.with_tests: python and js naming pairs, no repeats, cap, no-test sources, skipped dirs. Synthetic temp dirs only."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import plan_context as pc
from tests.support.tmpdirs import tracked_mkdtemp


def write(root, rel, text=""):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class WithTests(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def test_python_pair_found(self):
        write(self.d, "lib/ord.py", "x = 1\n")
        write(self.d, "tests/test_ord.py", "")
        self.assertEqual(pc.with_tests(self.d, ["lib/ord.py"]), ["lib/ord.py", "tests/test_ord.py"])

    def test_js_pairs_found_and_same_subtree_first(self):
        write(self.d, "web/audio.js", "")
        write(self.d, "web/tests/audio.test.js", "")
        write(self.d, "web/audio.test.js", "")
        got = pc.with_tests(self.d, ["web/audio.js"])
        self.assertEqual(got[1:], ["web/audio.test.js", "web/tests/audio.test.js"])

    def test_shortest_path_breaks_ties_outside_subtree(self):
        write(self.d, "web/audio.js", "")
        write(self.d, "other/deep/test_audio.py", "")
        write(self.d, "other/test_audio.py", "")
        self.assertEqual(pc.with_tests(self.d, ["web/audio.js"])[1:], ["other/test_audio.py", "other/deep/test_audio.py"])

    def test_tests_already_in_files_not_repeated(self):
        write(self.d, "lib/ord.py", "")
        write(self.d, "tests/test_ord.py", "")
        self.assertEqual(pc.with_tests(self.d, ["lib/ord.py", "tests/test_ord.py"]), ["lib/ord.py", "tests/test_ord.py"])

    def test_cap_respected(self):
        write(self.d, "lib/ord.py", "")
        for i in range(4):
            write(self.d, f"tests/d{i}/test_ord.py", "")
        self.assertEqual(len(pc.with_tests(self.d, ["lib/ord.py"], cap=2)), 3)
        self.assertEqual(len(pc.with_tests(self.d, ["lib/ord.py"], cap=6)), 5)

    def test_test_that_names_the_source_by_path_is_found(self):
        write(self.d, "web/session.js", "")
        write(self.d, "web/tests/exam-pass.test.js", "const s = fs.readFileSync(new URL('../session.js', import.meta.url));\n")
        write(self.d, "web/tests/other.test.js", "nothing here\n")
        self.assertEqual(pc.with_tests(self.d, ["web/session.js"]), ["web/session.js", "web/tests/exam-pass.test.js"])

    def test_short_stem_never_matches_by_reference(self):
        write(self.d, "web/app.js", "")
        write(self.d, "web/tests/x.test.js", "import '../app.js'\n")
        self.assertEqual(pc.with_tests(self.d, ["web/app.js"]), ["web/app.js"])

    def test_same_stem_test_comes_before_a_referencing_test(self):
        write(self.d, "web/stimulus.js", "")
        write(self.d, "web/tests/zz.test.js", "import '../stimulus.js'\n")
        write(self.d, "web/tests/stimulus.test.js", "")
        self.assertEqual(pc.with_tests(self.d, ["web/stimulus.js"])[1:], ["web/tests/stimulus.test.js", "web/tests/zz.test.js"])

    def test_source_without_test_adds_nothing(self):
        write(self.d, "lib/ord.py", "")
        write(self.d, "tests/test_other.py", "")
        self.assertEqual(pc.with_tests(self.d, ["lib/ord.py"]), ["lib/ord.py"])

    def test_test_and_doc_files_are_not_sources(self):
        write(self.d, "tests/test_ord.py", "")
        write(self.d, "docs/ord.md", "")
        write(self.d, "tests/test_ord_extra.py", "")
        self.assertEqual(pc.with_tests(self.d, ["tests/test_ord.py", "docs/ord.md"]), ["tests/test_ord.py", "docs/ord.md"])

    def test_skipped_dirs_ignored(self):
        write(self.d, "web/audio.js", "")
        write(self.d, "node_modules/x/audio.test.js", "")
        write(self.d, ".git/test_audio.py", "")
        self.assertEqual(pc.with_tests(self.d, ["web/audio.js"]), ["web/audio.js"])


if __name__ == "__main__":
    unittest.main()
