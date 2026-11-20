"""plan_lint: shared-file, test-env and ux-vars errors, the Affected tests line (synthetic repos only)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_files, plan_lint
from pipeline.configuration import WORK_DIR


class LintBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-lint-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, WORK_DIR, "s")
        os.makedirs(self.d)

    def write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def task(self, n, paths, wave=1, extra=""):
        with open(os.path.join(self.d, f"task-{n}.md"), "w", encoding="utf-8") as f:
            f.write(f"# T{n}\nPredicted paths: {paths}\n{extra}")
        return {"id": f"task-{n}", "wave": wave}

    def shared(self, *paths):
        ents = [self.task(i + 1, p, i + 1) for i, p in enumerate(paths)]
        return plan_check.shared_file_errors(plan_check._listings(self.d, ents)), plan_check.shared_warnings(self.d, ents)


class SharedFileTest(LintBase):
    def test_data_file_three_whole_tasks_is_error(self):
        errs, warns = self.shared("d/k.jsonl", "d/k.jsonl", "d/k.jsonl")
        self.assertEqual(len(errs), 1)
        self.assertTrue(errs[0].startswith("shared-file:") and "Fix:" in errs[0])
        self.assertEqual(warns, [])
        self.assertEqual(plan_check.errors_of(errs), errs)

    def test_rows_with_one_mass_is_owner_warning_only(self):
        errs, warns = self.shared("d/k.jsonl (mass)", "d/k.jsonl (rows)", "d/k.jsonl (rows)")
        self.assertEqual(errs, [])
        self.assertTrue(warns[0].startswith("shared:"))

    def test_all_rows_and_fragments_pass(self):
        self.assertEqual(self.shared("d/k.jsonl (rows)", "d/k.jsonl (rows)", "d/k.jsonl (rows)"), ([], []))
        self.assertEqual(self.shared("d/frag/", "d/frag/", "d/frag/"), ([], []))
        self.assertEqual(self.shared("d/*.jsonl", "d/*.jsonl", "d/*.jsonl"), ([], []))

    def test_two_tasks_not_an_error(self):
        self.assertEqual(self.shared("d/k.jsonl", "d/k.jsonl")[0], [])

    def test_registry_name_tokens(self):
        self.assertEqual(self.shared(*["t/weak-list-real.test.js"] * 3)[0], [])  # no error; the generic shared: warning may remain
        self.assertEqual(self.shared(*["src/playlist.js"] * 3)[0], [])
        self.assertEqual(len(self.shared(*["src/edited-ids.js"] * 3)[0]), 1)
        self.assertEqual(len(self.shared(*["src/word_list.js"] * 3)[0]), 1)


class AffectedTest(LintBase):
    def test_no_warning_and_line_optional(self):
        self.task(1, "src/a.py")
        self.assertFalse(hasattr(plan_lint, "affected_warnings"))
        self.assertFalse(hasattr(plan_check, "affected_warnings"))
        self.assertEqual(plan_check.errors_of(["affected: task-1: x"]), ["affected: task-1: x"])  # no longer a warning prefix

    def test_none_and_list_parse(self):
        self.assertEqual(plan_files.task_affected_tests("Affected tests: none\n"), "")
        self.assertEqual(plan_files.task_affected_tests("Goal: x\n"), "")
        self.assertEqual(plan_files.task_affected_tests("Affected tests: a.test.js\n"), "a.test.js")


class TestEnvTest(LintBase):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", self.root], check=True)
        self.write(".gitignore", "private/\n")
        self.write("private/data.json", "{}")
        self.write("pub/data.json", "{}")

    def run_task(self, test_file_text, test_cmd="node t/e2e.js --headless"):
        self.write("t/e2e.js", test_file_text)
        self.task(1, "t/e2e.js", extra=f"Test: {test_cmd}\n")
        return plan_lint.test_env_errors(self.d, "task-1", self.root)

    def test_ignored_path_is_error(self):
        got = self.run_task("const f = require('../private/data.json');\n")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("test-env:") and "synthetic fixture" in got[0])
        self.assertEqual(plan_check.errors_of(got), got)

    def test_workspace_style_literal_is_error(self):
        self.assertEqual(len(self.run_task('open("private/data.json")\n')), 1)

    def test_tracked_path_passes(self):
        self.assertEqual(self.run_task("read('../pub/data.json')\n"), [])

    def test_no_browser_word_passes(self):
        self.assertEqual(self.run_task("read('../private/data.json')\n", "node t/e2e.js"), [])

    def test_missing_test_file_and_missing_path_skip(self):
        self.task(1, "t/new.js", extra="Test: node t/new.js --headless\n")
        self.assertEqual(plan_lint.test_env_errors(self.d, "task-1", self.root), [])
        self.assertEqual(self.run_task("read('../private/none.json')\n"), [])

    def test_not_a_git_repo_skips(self):
        shutil.rmtree(os.path.join(self.root, ".git"))
        self.assertEqual(self.run_task("read('../private/data.json')\n"), [])

    def test_read_is_capped(self):
        self.assertEqual(self.run_task("x" * 300_000 + "\nread('../private/data.json')\n"), [])


class UxVarsTest(LintBase):
    def setUp(self):
        super().setUp()
        self.write("AUDIT.md", "```ux\nbuild: make <target>\nurl: http://x/<page>\n```\n")

    def tasks(self, text):
        self.write(f"{WORK_DIR}/s/TASKS.md", text)

    def test_missing_line_is_error_naming_vars(self):
        self.tasks("# s\nflags: ui\n")
        got = plan_lint.ux_vars_errors(self.d, self.root)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("ux-vars:") and "<page>" in got[0] and "<target>" in got[0])
        self.assertEqual(plan_check.errors_of(got), got)

    def test_line_missing_a_name_is_error(self):
        self.tasks("# s\nflags: ui\nUX-VARS: target=all\n")
        got = plan_lint.ux_vars_errors(self.d, self.root)
        self.assertIn("<page>", got[0])
        self.assertNotIn("<target>", got[0])

    def test_complete_line_no_ui_and_no_placeholder_pass(self):
        self.tasks("# s\nflags: ui\nUX-VARS: target=all page=home\n")
        self.assertEqual(plan_lint.ux_vars_errors(self.d, self.root), [])
        self.tasks("# s\nflags: docs\n")
        self.assertEqual(plan_lint.ux_vars_errors(self.d, self.root), [])
        self.write("AUDIT.md", "```ux\nurl: http://x/\n```\n")
        self.tasks("# s\nflags: ui\n")
        self.assertEqual(plan_lint.ux_vars_errors(self.d, self.root), [])


if __name__ == "__main__":
    unittest.main()
