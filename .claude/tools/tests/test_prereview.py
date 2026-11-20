"""Synthetic tests for the local pre-review gate. They pin the contract (FAIL -> dev, everything else -> reviewer), not the stage sequence."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localmodel import prereview
from pipeline import steps
from pipeline.util import read_jsonl
from tests.support.orch_harness import OrchHarness
from tests.support.tmpdirs import tracked_mkdtemp


class RealDefaultsTest(unittest.TestCase):
    """run() with its production defaults (real git, real lp_rc/usage_context); only the `lp review` process is faked."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = os.path.realpath(tmp.name)
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", self.d]
        subprocess.run([*g, "init", "-q"], check=True, capture_output=True)
        Path(self.d, "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run([*g, "add", "."], check=True, capture_output=True)
        subprocess.run([*g, "commit", "-qm", "x"], check=True, capture_output=True)
        Path(self.d, "a.txt").write_text("one\ntwo\n", encoding="utf-8")  # uncommitted change
        os.makedirs(os.path.join(self.d, ".work", "s"))
        Path(self.d, ".work", "s", "task-1.md").write_text("Goal: demo\nPredicted paths: a.txt\n", encoding="utf-8")

    def fake_lp(self, rc, out):
        real = subprocess.run

        def run(cmd, *a, **k):
            if cmd[0] == "git":  # the real diff: the FAIL quote is checked against it
                return real(cmd, *a, **k)
            if "review" in cmd:
                self.review_args = cmd
                return subprocess.CompletedProcess(cmd, rc, out, "")
            return subprocess.CompletedProcess(cmd, 0, "(none)\n", "")  # nav calls of the usage context

        return mock.patch("subprocess.run", side_effect=run)

    def test_uncommitted_diff_goes_to_lp_review_and_fail_comes_back(self):
        with self.fake_lp(1, "FAIL: wrong  | a.txt:2 two\n"):
            v, note = prereview.run("s", "task-1", self.d)
        self.assertEqual((v, note.splitlines()[0]), ("FAIL", "FAIL: wrong  | a.txt:2 two"))
        self.assertIn("diff-sha: ", note)  # the driver rebuilds the same uncommitted diff, so the sha marks an unchanged one
        self.assertIn("--diff", self.review_args)

    def test_pass_and_server_problem_go_to_the_reviewer(self):
        with self.fake_lp(0, "ok\n"):
            self.assertEqual(prereview.run("s", "task-1", self.d)[0], "DONE")
        with self.fake_lp(3, "server down\n"):
            self.assertIn("skipped (rc 3)", prereview.run("s", "task-1", self.d)[1])

    def test_temp_files_are_removed(self):
        before = set(os.listdir(tempfile.gettempdir()))
        with self.fake_lp(0, "ok\n"):
            prereview.run("s", "task-1", self.d)
        self.assertEqual({n for n in set(os.listdir(tempfile.gettempdir())) - before if n.endswith((".diff", ".md"))}, set())


class DecideTest(unittest.TestCase):
    def test_only_a_clear_fail_is_a_fail(self):
        self.assertEqual(prereview.decide(1, "FAIL  wrong sign | a.py:12 x = -1")[0], "FAIL")
        self.assertEqual(prereview.decide(1, "FAIL  wrong sign")[0], "DONE")  # no file:line quote: unclear, the paid reviewer decides
        self.assertEqual(prereview.decide(0, "PASS  ok")[0], "DONE")
        self.assertEqual(prereview.decide(3, "")[0], "SKIPPED")  # server problem: the paid reviewer runs
        self.assertEqual(prereview.decide(1, "IDK")[0], "SKIPPED")
        self.assertEqual(prereview.decide(2, "usage")[0], "SKIPPED")

    def test_predicted_paths_any_language(self):
        from pipeline.plan_files import paths_in

        self.assertEqual(paths_in("# t\nPredicted paths: `src/a.cpp`, include/, crates/x/src/lib.rs\n"), ["src/a.cpp", "include/", "crates/x/src/lib.rs"])
        self.assertEqual(paths_in("no such line"), [])


class RunTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/.work/s")
        with open(f"{self.d}/.work/s/task-1.md", "w") as f:
            f.write("# t\nPredicted paths: src/\n")

    def go(self, diff, rc, out, task="task-1"):
        return prereview.run("s", task, self.d, git=lambda *a, **k: diff, review=lambda *a, **k: (rc, out), context=lambda *a: "")[0]

    def test_committed_job_worktree_diff_is_reviewed(self):
        seen = []

        def review(repo, *a, **k):
            seen.append(a)
            return 0, "PASS"

        # work committed in the job branch, main checkout clean
        with mock.patch.object(prereview, "job_diff", return_value="=== repo . ===\n+x"):
            v = prereview.run("s", "task-1", self.d, git=lambda *a, **k: "", review=review, context=lambda *a: "")[0]
        self.assertEqual(len(seen), 1)  # the review ran: it was not skipped as "no diff"
        self.assertEqual(v, "DONE")

    def test_cut_job_diff_is_skipped_not_failed(self):
        with mock.patch.object(prereview, "job_diff", return_value="=== repo ===\n[... cut at 60000 characters; full diff: x]"):
            v, note = prereview.run("s", "task-1", self.d, git=lambda *a, **k: "", review=lambda *a, **k: (1, "FAIL a.py:1 x"), context=lambda *a: "")
        self.assertEqual(v, "SKIPPED")
        self.assertIn("too big", note)

    def test_over_budget_is_skipped_with_estimate_and_no_model_call(self):
        review = mock.Mock(return_value=(1, "FAIL"))
        v, note = prereview.run("s", "task-1", self.d, git=lambda *a, **k: "+x\n" * 200000, review=review, context=lambda *a: "")
        self.assertEqual(v, "SKIPPED")
        self.assertRegex(note, r"input ~\d+ est\. tokens > budget \d+")
        review.assert_not_called()

    def test_in_budget_diff_is_exactly_one_call(self):
        review = mock.Mock(return_value=(0, "PASS"))
        big = "+++ b/f.py\n" + "+x = 1\n" * 15000  # ~105 KB, was over the old 30000-char cap
        v, _ = prereview.run("s", "task-1", self.d, git=lambda *a, **k: big, review=review, context=lambda *a: "")
        self.assertEqual((v, review.call_count), ("DONE", 1))

    def test_every_verified_finding_is_listed_and_unverifiable_ones_dropped(self):
        diff = "+++ b/f.py\n@@ -1 +1,2 @@\n+a = 1\n+b = 2\n"
        out = "FAIL  2 problems\nrule one  | f.py:3 a = 1\nrule two  | f.py:4 b = 2\ninvented  | f.py:9 zzz"
        v, note = prereview.decide(1, out, diff=diff)
        self.assertEqual(v, "FAIL")
        self.assertIn("a = 1", note)
        self.assertIn("b = 2", note)
        self.assertNotIn("zzz", note)
        v, note = prereview.decide(1, "FAIL  x\nbad  | f.py:9 zzz", diff=diff)  # only unverifiable: unclear, passed on
        self.assertEqual(v, "DONE")
        self.assertIn("unclear", note)

    def test_usage_context_reaches_the_reviewer_and_a_failure_of_it_does_not_block(self):
        seen = []

        def review(repo, *a, **k):
            seen.append(Path(a[a.index("--brief") + 1]).read_text(encoding="utf-8"))
            return 0, "PASS"

        prereview.run("s", "task-1", self.d, git=lambda *a, **k: "+x\n", review=review, context=lambda *a: "\n## Other files\n- f: g.cpp\n")
        self.assertIn("## Other files", seen[0])
        self.assertIn("Predicted paths", seen[0])

        def boom(*a):
            raise RuntimeError("x")

        prereview.run("s", "task-1", self.d, git=lambda *a, **k: "+x\n", review=review, context=boom)
        self.assertNotIn("## Other files", seen[1])

    def test_fail_pass_empty_and_error_paths(self):
        self.assertEqual(self.go("+++ b/f.py\n+x\n", 1, "FAIL bad | f.py:3 x"), "FAIL")
        self.assertEqual(self.go("+x\n", 1, "FAIL bad | f.py:3 x"), "DONE")  # the quoted file is not in the diff: unclear
        self.assertEqual(self.go("+x\n", 1, "FAIL bad"), "DONE")
        self.assertEqual(self.go("+x\n" * 200000, 1, "FAIL bad | f.py:3 x"), "SKIPPED")  # too big: skipped, not FAIL
        self.assertEqual(self.go("+x\n", 0, "PASS ok"), "DONE")
        self.assertEqual(self.go("", 1, "FAIL bad"), "SKIPPED")  # no diff: nothing to judge
        self.assertEqual(self.go("+x\n", 3, ""), "SKIPPED")
        self.assertEqual(self.go("+x\n", None, ""), "SKIPPED")  # command could not run
        self.assertEqual(self.go("+x", 1, "FAIL", task="task-9"), "SKIPPED")  # missing brief file


class RoutingTest(OrchHarness):
    """With the gate on: FAIL -> dev with the reason, DONE -> reviewer, and the gate runs before every paid review. With it off: no gate."""

    def dev_done(self):
        self.answer(self.next(), "VERDICT: DONE")

    def test_gate_fail_goes_to_dev_then_reviewer(self):
        self.config(prereview=True)
        self.dev_done()
        g = self.next()
        self.assertEqual(g["agent"], "script")
        self.assertIn("prereview.py", g["prompt"])
        self.answer(g, "VERDICT: FAIL\nFAIL wrong sign a.py:1")
        d = self.next()
        self.assertEqual(d["agent"], "dev")
        self.assertIn("report-" + g["id"], d["prompt"])
        self.answer(d, "VERDICT: DONE")
        g2 = self.next()
        self.assertEqual(g2["agent"], "script")  # the gate runs again after the rework
        self.assertIn("prereview.py", g2["prompt"])
        self.answer(g2, "VERDICT: DONE")
        self.assertEqual(self.next()["agent"], "reviewer")

    def test_gate_fail_is_not_a_dev_try_and_never_escalates(self):
        self.config(prereview=True)
        self.dev_done()
        models: list = []
        for _ in range(12):  # the gate always says FAIL: two free reworks on the same model, then no gate, then the paid reviewer
            n = self.next()
            if n["agent"] == "reviewer":
                self.assertEqual(models, ["haiku", "haiku"])
                evs = [e for e in read_jsonl(f"{self.d}/.work/s/orch.jsonl") if e["ev"] == "dispatch" and e["role"] == "dev"]
                self.assertEqual([e["free"] for e in evs], [False, True, True])
                return
            if n["agent"] == "dev":
                models.append(n["model"])
            self.answer(n, "VERDICT: FAIL\nFAIL again a.py:1" if n["agent"] == "script" else "VERDICT: DONE")
        self.fail("the gate kept the task away from the paid reviewer")

    def test_gate_skipped_when_the_rework_left_the_diff_unchanged(self):
        self.config(prereview=True)
        self.dev_done()
        g = self.next()
        sha = steps.hashlib.sha256(b"same").hexdigest()[:16]
        self.answer(g, f"VERDICT: FAIL\nFAIL x a.py:1\ndiff-sha: {sha}")
        self.answer(self.next(), "VERDICT: DONE")
        with mock.patch.object(steps, "job_diff", return_value="same"):
            self.assertEqual(self.next()["agent"], "reviewer")

    def test_gate_done_goes_to_reviewer(self):
        self.config(prereview=True)
        self.dev_done()
        self.answer(self.next(), "VERDICT: DONE")
        self.assertEqual(self.next()["agent"], "reviewer")

    def test_gate_off_by_default(self):
        self.dev_done()
        self.assertEqual(self.next()["agent"], "reviewer")


if __name__ == "__main__":
    unittest.main()
