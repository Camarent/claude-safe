"""audit_run.suppressions: added lint/clippy suppressions of a diff become findings (real tmp repo)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_run


class SuppressionsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.g = ["git", "-C", self.d, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*self.g, "init", "-q", "-b", "master"], check=True)
        self.write("a.rs", "fn a() {}\n")
        subprocess.run([*self.g, "add", "-A"], check=True)
        subprocess.run([*self.g, "commit", "-qm", "init"], check=True)
        subprocess.run([*self.g, "switch", "-q", "-c", "w"], check=True)

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_added_allow_and_noqa_are_listed_with_lines(self):
        self.write("a.rs", "fn a() {}\n#[allow(dead_code)]\nfn b() {}\n")
        self.write("b.py", "import os  # noqa: F401\nx = 1\n")
        subprocess.run([*self.g, "add", "-A"], check=True)
        subprocess.run([*self.g, "commit", "-qm", "w"], check=True)
        got = {(f["file"], f["line"]) for f in audit_run.suppressions(self.d, "master")}
        self.assertEqual(got, {("a.rs", 2), ("b.py", 1)})

    def test_c_forms_and_quoted_paths(self):
        self.write('q"x.cpp', "int a;\n#pragma warning(disable: 4996)\nint b; // NOLINT\n")
        self.write("b.py", "x: int = f()  # type: ignore\n")
        subprocess.run([*self.g, "add", "-A"], check=True)
        subprocess.run([*self.g, "commit", "-qm", "w"], check=True)
        got = {(f["file"], f["line"]) for f in audit_run.suppressions(self.d, "master")}
        self.assertEqual(got, {('q"x.cpp', 2), ('q"x.cpp', 3), ("b.py", 1)})

    def test_no_new_suppression_no_finding(self):
        self.write("a.rs", "fn a() {}\nfn c() {}\n")
        subprocess.run([*self.g, "commit", "-qam", "w"], check=True)
        self.assertEqual(audit_run.suppressions(self.d, "master"), [])


if __name__ == "__main__":
    unittest.main()
