"""plan_outputs / plan_kinds: a code task's Predicted paths must be tracked repo files (not work-folder scratch, not git-ignored); report tasks keep the `.work/` rule."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_kinds, plan_outputs

TID = "task-1"  # task id; asserts use this name, not the literal (path-dependent check)


class OutputPathsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        with open(os.path.join(self.root, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("build/\n")
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def task(self, name, body):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(body)

    def test_relative_work_path_fails_code_task(self):
        self.task(f"{TID}.md", "Predicted paths: .work/s/out.md\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn(f"outputs: {TID}: output `.work/s/out.md` is under the work folder", probs[0])

    def test_absolute_main_tree_work_path_fails_code_task(self):
        self.task(f"{TID}.md", f"Predicted paths: {os.path.join(self.root, '.work', 's', 'out.md')}\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertNotIn("absolute main-tree", probs[0])
        self.assertIn("write outputs to a tracked file or the task report", probs[0])

    def test_git_ignored_path_fails_code_task(self):
        self.task(f"{TID}.md", "Predicted paths: build/out.bin, src/a.py\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("`build/out.bin` is under the work folder or git-ignored", probs[0])

    def test_outputs_header_git_ignored_path_fails_code_task(self):
        self.task(f"{TID}.md", "Predicted paths: src/a.py\nOutputs: build/out.bin, 3\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("`build/out.bin` is under the work folder or git-ignored", probs[0])

    def test_outside_repo_path_does_not_hide_ignored_path(self):
        outside = os.path.join(os.path.dirname(self.root), "elsewhere.txt")
        self.task(f"{TID}.md", f"Predicted paths: {outside}, build/out.bin\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("`build/out.bin` is under the work folder or git-ignored", probs[0])

    def test_done_work_path_fails_code_task(self):
        self.task(f"{TID}.md", "Predicted paths: src/a.py\nDone: write the summary to .work/s/x.md\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("output `.work/s/x.md` is under the work folder", probs[0])

    def test_outputs_header_work_path_fails_code_task(self):
        self.task(f"{TID}.md", "Predicted paths: src/a.py\nOutputs: `.work/s/out.md`\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("output `.work/s/out.md` is under the work folder", probs[0])

    def test_done_ignored_path_fails_ignored_done(self):
        self.task(f"{TID}.md", "Predicted paths: src/a.py\nDone: write the later notes to build/notes.md\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(f"ignored-done: {TID}: path `build/notes.md`"))
        self.assertEqual(plan_check.errors_of(probs), probs)

    def test_done_tracked_and_covered_work_paths_pass_ignored_done(self):
        self.task(f"{TID}.md", "Predicted paths: src/a.py\nOutputs: .work/s/out.md\nDone: edit src/a.py and write .work/s/out.md\n")
        probs = plan_outputs.check_output_paths(self.d, TID, self.root)
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith(f"outputs: {TID}: output `.work/s/out.md`"))

    def test_url_on_done_line_is_not_a_path(self):
        self.task(f"{TID}.md", "Predicted paths: src/a.py\nDone: cite https://x/build/y.md in the summary\n")
        self.assertEqual(plan_outputs.check_output_paths(self.d, TID, self.root), [])

    def test_tracked_decoy_with_work_in_name_passes(self):
        self.task(f"{TID}.md", "Predicted paths: docs/networking.work.md\n")
        self.assertEqual(plan_outputs.check_output_paths(self.d, TID, self.root), [])

    def test_report_task_keeps_work_rule(self):
        self.task(f"{TID}.md", "Type: report\nPredicted paths: .work/s/out.md\n")
        probs = plan_kinds.check_kinds(self.d, {"id": TID}, [], self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn("is a relative .work path", probs[0])

    def test_report_task_absolute_main_tree_path_passes(self):
        self.task(f"{TID}.md", f"Type: report\nPredicted paths: {os.path.join(self.root, '.work', 's', 'out.md')}\n")
        self.assertEqual(plan_kinds.check_kinds(self.d, {"id": TID}, [], self.root), [])

    def test_code_task_routed_to_output_rule(self):
        self.task(f"{TID}.md", "Predicted paths: build/out.bin\n")
        probs = plan_kinds.check_kinds(self.d, {"id": TID}, [], self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn(f"outputs: {TID}", probs[0])
