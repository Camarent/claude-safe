"""audit_audit_phases.sec_audit_needed: the Opus sec audit runs only when the diff touches a configured security path (real tmp repo)."""

import argparse
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases


class SecAuditGateTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        self.g = ["git", "-C", self.d, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*self.g, "init", "-q", "-b", "master"], check=True)
        subprocess.run([*self.g, "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        subprocess.run([*self.g, "checkout", "-q", "-b", "dev"], check=True)
        self.a = argparse.Namespace(dir=self.d, stage="s")
        self.cfg = {"branch_release": "master", "branch_work": "dev"}

    def commit(self, rel):
        os.makedirs(os.path.dirname(os.path.join(self.d, rel)) or self.d, exist_ok=True)
        with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        subprocess.run([*self.g, "add", "-A"], check=True)
        subprocess.run([*self.g, "commit", "-q", "-m", rel], check=True)

    def needed(self, **cfg):
        return audit_phases.sec_audit_needed(self.a, {**self.cfg, **cfg}, self.d)

    def test_unset_uses_the_default_paths(self):
        self.commit("docs/readme.md")
        self.assertFalse(self.needed())
        self.commit("src/auth/login.py")
        self.assertTrue(self.needed())

    def test_default_content_match_runs_the_audit(self):
        self.commit("lib/a.py")
        self.assertFalse(self.needed())
        self.append("lib/a.py", "subprocess.run(x)\n")
        self.assertTrue(self.needed())

    def append(self, rel, text):
        with open(os.path.join(self.d, rel), "a", encoding="utf-8") as fh:
            fh.write(text)
        subprocess.run([*self.g, "commit", "-qam", "edit"], check=True)

    def test_docs_and_tests_are_not_content_scanned(self):
        self.commit("docs/readme.md")
        self.commit("tests/test_a.py")
        self.append("docs/readme.md", "subprocess.run(x) innerHTML\n")
        self.append("tests/test_a.py", "pickle.loads(x)\n")
        self.assertFalse(self.needed())

    def test_new_content_patterns(self):
        for i, line in enumerate(("el.innerHTML = x", "yaml.load(f)", "unsafe { p }", "fetch(u)", "shutil.rmtree(d)")):
            self.commit(f"lib/m{i}.py")
            self.assertFalse(self.needed(), line)
            self.append(f"lib/m{i}.py", line + "\n")
            self.assertTrue(self.needed(), line)
            subprocess.run([*self.g, "reset", "-q", "--hard", "master"], check=True)

    def test_deleted_code_line_triggers_but_pure_rename_does_not(self):
        self.commit("lib/a.py")
        subprocess.run([*self.g, "branch", "-f", "master", "dev"], check=True)
        subprocess.run([*self.g, "mv", "lib/a.py", "lib/b.py"], check=True)
        subprocess.run([*self.g, "commit", "-qm", "mv"], check=True)
        self.assertFalse(self.needed())
        with open(os.path.join(self.d, "lib", "b.py"), "w", encoding="utf-8") as fh:
            fh.write("")
        subprocess.run([*self.g, "commit", "-qam", "del"], check=True)
        self.assertTrue(self.needed())

    def test_path_matching_is_case_insensitive_and_word_bound(self):
        self.commit("docs/a.md")
        for rel, hit in (
            ("src/AUTH.py", True),
            ("data/x.json", True),
            ("a/b.lock", True),
            ("web/app.ts", True),
            (".gitignore", True),
            ("lib/network.py", False),
            ("lib/curly.py", False),
            ("lib/pathology.py", False),
            ("lib/run_cmd.py", True),
            ("docs/x.yml", False),
            ("docs/y.sh", False),
        ):
            self.assertEqual(audit_phases.sec_path_hit(rel, audit_phases.SEC_AUDIT_PATHS), hit, rel)

    def test_star_always_runs(self):
        self.commit("docs/readme.md")
        self.assertTrue(self.needed(sec_audit_paths=["*"]))

    def test_no_match_skips_the_audit(self):
        self.commit("docs/readme.md")
        self.assertFalse(self.needed(sec_audit_paths=["src/auth/*", "*.yml"]))

    def test_match_runs_the_audit(self):
        self.commit("docs/readme.md")
        self.commit("src/auth/login.py")
        self.assertTrue(self.needed(sec_audit_paths=["src/auth/*"]))

    def test_empty_diff_or_bad_ref_keeps_the_audit(self):
        self.assertTrue(self.needed(sec_audit_paths=["src/auth/*"]))
        self.assertTrue(self.needed(sec_audit_paths=["src/auth/*"], branch_release="nope"))


if __name__ == "__main__":
    unittest.main()
