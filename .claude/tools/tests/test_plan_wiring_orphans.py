"""pipeline.plan_wiring_orphans: a new source file under wiring_dirs that no other tracked file imports is an orphan. Temp git repos, no model."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_wiring_orphans
from pipeline.configuration import WIRING_NONE_TEXT

DIRS = ["ui"]
ROUTER = "ui/router.ts"
OVERLAY = "ui/overlay.ts"
KNIP_JSON = "knip.json"


def git(repo, *a):
    subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)


class Repo(unittest.TestCase):
    def make(self, base_files, head_files):
        """-> (repo dir, base sha): BASE_FILES committed first, then HEAD_FILES (path -> text, added or overwritten) committed on top."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = tmp.name
        git(repo, "init", "-q")
        for path, text in base_files.items():
            self.write(repo, path, text)
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        base = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        for path, text in head_files.items():
            self.write(repo, path, text)
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "head")
        return repo, base

    def write(self, repo, path, text):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)


class TestOrphans(Repo):
    def test_new_file_imported_by_nobody_is_listed(self):
        repo, base = self.make({"README.md": "x\n"}, {OVERLAY: "export const o = 1;\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [OVERLAY])

    def test_new_file_imported_from_router_is_not_listed(self):
        repo, base = self.make({ROUTER: "export const r = 1;\n"}, {ROUTER: 'import { o } from "./overlay";\n', OVERLAY: "export const o = 1;\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])

    def test_name_in_comment_or_string_is_not_an_importer(self):
        decoy = '// import { o } from "./overlay";\nconst s = "from overlay import x";\n'
        repo, base = self.make({ROUTER: decoy}, {OVERLAY: "export const o = 1;\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [OVERLAY])

    def test_python_import_counts(self):
        repo, base = self.make({"ui/app.py": "x = 1\n"}, {"ui/helper.py": "y = 2\n", "ui/app.py": "from ui.helper import y\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])

    def test_same_stem_in_another_module_does_not_hide_orphan(self):
        # `@/index/x` and `./other/index` name other modules: ui/index.ts stays unimported
        repo, base = self.make({ROUTER: 'import a from "@/index/x";\nimport b from "./other/index";\n'}, {"ui/index.ts": "export {};\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), ["ui/index.ts"])

    def test_path_alias_import_counts(self):
        repo, base = self.make({ROUTER: "export const r = 1;\n"}, {ROUTER: 'import { o } from "@/ui/overlay";\n', OVERLAY: "export const o = 1;\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])

    def test_python_relative_import_counts(self):
        repo, base = self.make({"ui/app.py": "x = 1\n"}, {"ui/helper.py": "y = 2\n", "ui/app.py": "from . import helper\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])

    def test_non_ascii_names_are_checked_and_count_as_imports(self):
        # git quotes non-ASCII names in plain output: the importer and the import must still be seen
        importer = "ui/naïve.ts"
        repo, base = self.make({"README.md": "x\n"}, {"ui/café.ts": "export {};\n", importer: 'import { c } from "./café";\n'})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [importer])

    def test_test_files_and_knip_entries_are_never_orphans(self):
        repo, base = self.make(
            {"README.md": "x\n"},
            {"ui/test_thing.py": "x = 1\n", "ui/main.ts": "export {};\n", KNIP_JSON: json.dumps({"entry": ["ui/main.ts"]}) + "\n"},
        )
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])

    def test_file_outside_wiring_dirs_is_not_listed(self):
        repo, base = self.make({"README.md": "x\n"}, {"lib/loose.ts": "export {};\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])

    def test_no_new_files_gives_empty_list(self):
        repo, base = self.make({ROUTER: "export const r = 1;\n"}, {ROUTER: "export const r = 2;\n"})
        self.assertEqual(plan_wiring_orphans.orphans(repo, base, DIRS), [])


class TestMain(Repo):
    def test_writes_one_line_per_orphan(self):
        repo, base = self.make({"README.md": "x\n"}, {OVERLAY: "export const o = 1;\n"})
        self.write(repo, "pipeline.json", json.dumps({"wiring_dirs": DIRS}) + "\n")
        out = os.path.join(repo, "orphans.md")
        self.assertEqual(plan_wiring_orphans.main(["--workspace", repo, "--base", base, "--out", out]), 0)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), f"- orphan: {OVERLAY}\n")

    def test_no_orphans_writes_none_statement(self):
        repo, base = self.make({ROUTER: "export const r = 1;\n"}, {ROUTER: 'import { o } from "./overlay";\n', OVERLAY: "export const o = 1;\n"})
        self.write(repo, "pipeline.json", json.dumps({"wiring_dirs": DIRS}) + "\n")
        out = os.path.join(repo, "orphans.md")
        self.assertEqual(plan_wiring_orphans.main(["--workspace", repo, "--base", base, "--out", out]), 0)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), WIRING_NONE_TEXT)

    def test_broken_config_or_bad_base_exits_zero_and_writes_nothing(self):
        repo, base = self.make({"README.md": "x\n"}, {OVERLAY: "export const o = 1;\n"})
        out = os.path.join(repo, "orphans.md")
        self.write(repo, "pipeline.json", "{not json\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(plan_wiring_orphans.main(["--workspace", repo, "--base", base, "--out", out]), 0)
        self.assertIn("orphan check skipped", err.getvalue())
        self.assertFalse(os.path.exists(out))
        self.write(repo, "pipeline.json", json.dumps({"wiring_dirs": DIRS}) + "\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(plan_wiring_orphans.main(["--workspace", repo, "--base", "no-such-commit", "--out", out]), 0)
        self.assertIn("orphan check skipped", err.getvalue())
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
