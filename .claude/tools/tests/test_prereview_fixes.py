"""Pre-review: free rounds count the whole log, no diff -> no skip crash, memoized job diff, sha only for a job diff."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import prereview
from pipeline import steps, worktrees


def fail_events(tmp, n):
    evs = []
    for i in range(n):
        f = os.path.join(tmp, f"r{i}.md")
        with open(f, "w") as fh:
            fh.write("VERDICT: FAIL\nx a.py:1\n")
        evs += [
            {"ev": "dispatch", "task": "t", "id": f"p{i}", "role": "script", "why": "local pre-review (lp review)"},
            {"ev": "report", "task": "t", "id": f"p{i}", "role": "script", "verdict": "FAIL", "file": f},
        ]
    return evs


class NoCommitRework(unittest.TestCase):
    def evs(self, head):
        return [
            {"ev": "dispatch", "task": "t", "id": "r1", "role": "reviewer", "head": head},
            {"ev": "report", "task": "t", "id": "r1", "role": "reviewer", "verdict": "FAIL", "file": "f.md"},
            {"ev": "dispatch", "task": "t", "id": "d2", "role": "dev"},
            {"ev": "report", "task": "t", "id": "d2", "role": "dev", "verdict": "DONE", "file": "d.md"},
        ]

    def ask(self, evs, now):
        a = SimpleNamespace(stage="s", dir=".")
        with mock.patch.object(steps, "job_heads", return_value=now), mock.patch.object(steps, "hint", return_value=""):
            return steps._no_commit_ask(a, {"id": "t"}, evs)

    def test_same_head_asks_the_lead(self):
        r = self.ask(self.evs("x=aaa"), "x=aaa")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("rework added no commit since reviewer FAIL r1", r["message"])

    def test_new_commit_or_first_review_dispatches(self):
        self.assertIsNone(self.ask(self.evs("x=aaa"), "x=bbb"))
        self.assertIsNone(self.ask(self.evs(""), ""))  # no recorded head (worktrees off)
        self.assertIsNone(self.ask(self.evs("x=aaa")[2:], "x=aaa"))  # no earlier reviewer FAIL

    def test_job_heads_empty_without_worktrees(self):
        with mock.patch.object(worktrees, "job_info", return_value=None):
            self.assertEqual(worktrees.job_heads(SimpleNamespace(stage="s", dir="."), "t"), "")


class PrereviewFixes(unittest.TestCase):
    def test_free_rounds_survive_an_auto_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs = fail_events(tmp, 2)
            evs.insert(2, {"ev": "retry", "task": "t", "auto": True})  # history() would drop the first round
            mine = steps.history(evs, "t")
            a = SimpleNamespace(orch_events=evs, stage="s", dir=tmp)
            self.assertTrue(steps._prereview_done(a, {"id": "t"}, mine))

    def test_missing_job_diff_does_not_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs = fail_events(tmp, 1)
            with open(evs[1]["file"], "a") as fh:
                fh.write("diff-sha: abcdef\n")
            a = SimpleNamespace(orch_events=evs, stage="s", dir=tmp)
            with mock.patch.object(steps, "job_diff", return_value=None):
                self.assertFalse(steps._prereview_done(a, {"id": "t"}, evs))

    def test_job_diff_memoized_per_decide(self):
        a = SimpleNamespace(wt_memo={}, stage="s", dir=".")
        with mock.patch.object(worktrees, "job_info", return_value={"root": "x"}), mock.patch.object(worktrees.wt, "diff", return_value="D") as d:
            self.assertEqual(worktrees.job_diff(a, "t", 5), "D")
            self.assertEqual(worktrees.job_diff(a, "t", 5), "D")
        self.assertEqual(d.call_count, 1)

    def test_rc3_is_skip_and_too_big_skips_before_context(self):
        self.assertEqual(prereview.decide(3, "skipped: diff too big")[0], "SKIPPED")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".work", "s"))
            with open(os.path.join(tmp, ".work", "s", "t.md"), "w") as fh:
                fh.write("Goal\n")
            big = "diff --git a/a b/a\n+" + "x" * 400000
            ctx = mock.Mock(return_value="")
            with mock.patch.object(prereview, "job_diff", return_value=big):
                v, note = prereview.run("s", "t", tmp, review=mock.Mock(), context=ctx)
            self.assertEqual(v, "SKIPPED")
            self.assertIn("est. tokens > budget", note)
            ctx.assert_not_called()

    def test_fail_quote_must_be_in_the_diff(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old = 1\n+new = 2\n"
        self.assertEqual(prereview.decide(1, "FAIL  bad  | a.py:1 new = 2", diff)[0], "FAIL")
        self.assertEqual(prereview.decide(1, "FAIL  bad  | a.py:1 old = 1", diff)[0], "FAIL")
        for ev in ("b.py:1 new = 2", "a.py:1 invented()", "a.py:1"):  # file not in diff, text not in diff, no text
            self.assertEqual(prereview.decide(1, f"FAIL  see c.py:9 | {ev}", diff)[0], "DONE", ev)

    def test_uncommitted_diff_sha_matches_driver(self):
        import subprocess

        from pipeline.configuration import DIFF_SHA_LEN

        with tempfile.TemporaryDirectory() as tmp:
            for c in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "n"]):
                subprocess.run(["git", *c], cwd=tmp, check=True)
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("one\n")
            subprocess.run(["git", "add", "a.txt"], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("two\n")
            os.makedirs(os.path.join(tmp, ".work", "s"))
            with open(os.path.join(tmp, ".work", "s", "t.md"), "w") as fh:
                fh.write("Goal\nPredicted paths: a.txt\n")
            _v, note = prereview.run("s", "t", tmp, review=lambda *x, **k: (1, "FAIL x | a.txt:1 two"), context=lambda *x: "")
            self.assertIn("diff-sha: ", note)
            self.assertEqual(len(note.split("diff-sha: ")[1]), DIFF_SHA_LEN)
            evs = fail_events(tmp, 1)
            with open(evs[1]["file"], "a") as fh:
                fh.write(note.split("\n")[-1] + "\n")
            a = SimpleNamespace(orch_events=evs, stage="s", dir=tmp)
            self.assertTrue(steps._prereview_done(a, {"id": "t"}, evs))  # unchanged tree: no second pre-review
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("three\n")
            self.assertFalse(steps._prereview_done(a, {"id": "t"}, evs))  # the dev changed it

    def test_sha_only_for_job_diff(self):
        def rev(*x, **k):
            return 1, "FAIL x | a.txt:1 y"

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".work", "s"))
            with open(os.path.join(tmp, ".work", "s", "t.md"), "w") as fh:
                fh.write("Goal\n")
            with mock.patch.object(prereview, "job_diff", return_value="+++ b/a.txt\n+y\n"):
                _v, note = prereview.run("s", "t", tmp, review=rev, context=lambda *x: "")
            self.assertIn("diff-sha: ", note)


if __name__ == "__main__":
    unittest.main()
