"""Docs fix by script: a repeated docs-only one-line finding is applied once, only when its old text occurs exactly once; anything else is left to the review round."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, steps, steps_docfix
from pipeline.configuration import EV_DISPATCH, EV_REPORT, FAIL

LINE = "- `docs/guide.md`: `Run the old command.` -> `Run the new command.`"


class PairParsing(unittest.TestCase):
    def test_same_line_in_both_reports_is_repeated(self):
        self.assertEqual(steps_docfix.repeated_pair(LINE, f"FAIL\n{LINE}\n"), ("docs/guide.md", "Run the old command.", "Run the new command."))

    def test_different_or_missing_line_is_not_repeated(self):
        other = LINE.replace("old", "other")
        self.assertIsNone(steps_docfix.repeated_pair(LINE, other))
        self.assertIsNone(steps_docfix.repeated_pair(LINE, "VERDICT: FAIL\nno pair here"))

    def test_two_pair_lines_in_one_report_is_not_docs_only_one_line(self):
        two = f"{LINE}\n{LINE}"
        self.assertIsNone(steps_docfix.repeated_pair(two, two))


class ApplyOnce(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "docs"))

    def write(self, text):
        with open(os.path.join(self.root, "docs", "guide.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def read(self):
        with open(os.path.join(self.root, "docs", "guide.md"), encoding="utf-8") as f:
            return f.read()

    def test_unique_old_text_is_applied(self):
        self.write("Intro.\nRun the old command.\n")
        ok, _ = steps_docfix.apply_pair("docs/guide.md", "Run the old command.", "Run the new command.", self.root)
        self.assertTrue(ok)
        self.assertEqual(self.read(), "Intro.\nRun the new command.\n")

    def test_decoy_with_two_occurrences_is_not_applied(self):
        text = "Run the old command.\nAgain: Run the old command.\n"
        self.write(text)
        ok, why = steps_docfix.apply_pair("docs/guide.md", "Run the old command.", "Run the new command.", self.root)
        self.assertFalse(ok)
        self.assertIn("occurs 2 times", why)
        self.assertEqual(self.read(), text)

    def test_missing_old_text_is_not_applied(self):
        self.write("nothing to see\n")
        ok, _ = steps_docfix.apply_pair("docs/guide.md", "Run the old command.", "Run the new command.", self.root)
        self.assertFalse(ok)
        self.assertEqual(self.read(), "nothing to see\n")

    def test_path_outside_the_worktree_is_refused(self):
        ok, why = steps_docfix.apply_pair("../guide.md", "a", "b", self.root)
        self.assertFalse(ok)
        self.assertIn("not usable", why)


class ScriptCommits(unittest.TestCase):
    def test_main_applies_and_commits_in_the_worktree(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ.get("PATH", "")}
        git = ["git", "-C", root]
        subprocess.run([*git, "init", "-q"], check=True, env=env)
        with open(os.path.join(root, "guide.md"), "w", encoding="utf-8") as f:
            f.write("Run the old command.\n")
        subprocess.run([*git, "add", "guide.md"], check=True, env=env)
        subprocess.run([*git, "commit", "-q", "-m", "init"], check=True, env=env)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(root)
        self.assertEqual(steps_docfix.main(["--file", "guide.md", "--old", "old", "--new", "new"]), 0)
        log = subprocess.run([*git, "log", "--oneline"], check=True, capture_output=True, text=True, env=env).stdout
        self.assertEqual(log.count("\n"), 2)
        with open(os.path.join(root, "guide.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "Run the new command.\n")


class DispatchGlue(unittest.TestCase):
    def fail_event(self, i, text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        f = os.path.join(d, f"report-{i}.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(text)
        return [
            {"ev": EV_DISPATCH, "id": f"rev-{i}", "role": "reviewer", "agent": "reviewer", "model": "haiku", "task": "task-1"},
            {"ev": EV_REPORT, "id": f"rev-{i}", "role": "reviewer", "verdict": FAIL, "file": f, "task": "task-1"},
        ]

    def test_repeated_line_dispatches_the_script_once(self):
        mine = self.fail_event(1, LINE) + self.fail_event(2, LINE)
        last = mine[-1]
        d = steps._docfix_dispatch(None, {"id": "task-1"}, mine, "rid-3", last)
        self.assertIsNotNone(d)
        self.assertEqual(d and d["agent"], "script")
        self.assertIn("steps_docfix.py --file docs/guide.md", (d or {}).get("prompt", ""))
        mine.append({"ev": EV_DISPATCH, "id": "rid-3", "role": "script", "agent": "script", "task": "task-1", "why": steps_docfix.DOCFIX_WHY})
        self.assertIsNone(steps._docfix_dispatch(None, {"id": "task-1"}, mine, "rid-4", last))

    def test_single_fail_is_not_a_repeat(self):
        mine = self.fail_event(1, LINE)
        self.assertIsNone(steps._docfix_dispatch(None, {"id": "task-1"}, mine, "rid-2", mine[-1]))


class AuditorFirstPassRetry(unittest.TestCase):
    def test_auditor_blocked_without_verdict_is_retried_once(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        log = os.path.join(tmp, "orch.jsonl")
        d = {"id": "audit-sec-1", "task": "audit-sec", "role": "sec-auditor", "agent": "sec-auditor"}
        self.assertTrue(cli_orch._auto_retry(log, [], d, "no clear verdict"))
        self.assertFalse(cli_orch._auto_retry(log, [{"ev": "retry", "auto": True, "task": "audit-sec", "role": "sec-auditor"}], d, "no clear verdict"))


if __name__ == "__main__":
    unittest.main()
