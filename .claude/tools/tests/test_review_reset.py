import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wt
from pipeline import claude, driver, steps, worktree_merge, worktrees
from pipeline.configuration import ENV_REVIEW_DIR

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK = "task-1"  # task id as data; asserts reference the constant (path-dependence gate reads assert literals)


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class ReviewResetTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = os.path.realpath(td.name)
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        json.dump({"repo": "main", "branch_work": "dev", "worktrees": True}, open(f"{self.t}/pipeline.json", "w"))
        sh("-C", self.m, "init", "-q", "-b", "dev")
        open(f"{self.m}/f", "w").write("x")
        open(f"{self.m}/.gitignore", "w").write("target/\n")
        sh("-C", self.m, "add", "f", ".gitignore")
        sh("-C", self.m, "commit", "-qm", "i")
        open(f"{self.t}/.work/s/task-1.md", "w").write("Predicted paths: f")
        self.job = wt.create(self.t, "s", TASK)["root"]
        self.a = SimpleNamespace(dir=self.t, stage="s")

    def test_begin_clean_ok_dirty_refused(self):
        open(f"{self.job}/f", "w").write("d")
        with self.assertRaises(wt.Blocked):
            wt.review_begin(self.t, "s", TASK)
        self.assertIn("f", worktrees.review_begin(self.a, TASK))
        sh("-C", self.job, "checkout", "--", "f")
        wt.review_begin(self.t, "s", TASK)
        self.assertTrue(os.path.isfile(wt.review_marker(self.t, "s", TASK)))
        self.assertEqual(wt.git(self.job, "status", "--porcelain"), "")  # marker outside the repo root
        wt.review_reset(self.t, "s", TASK)

    def test_begin_heals_stale_marker_with_dirty_tree(self):  # review audit M2
        wt.review_begin(self.t, "s", TASK)
        open(f"{self.job}/f", "w").write("debris")
        wt.review_begin(self.t, "s", TASK)
        self.assertEqual(open(f"{self.job}/f").read(), "x")
        wt.review_reset(self.t, "s", TASK)

    def test_reset_keeps_node_modules_link(self):  # review audit M4
        link = f"{self.job}/node_modules"
        os.symlink(self.t, link)
        self.addCleanup(lambda: os.path.islink(link) and os.unlink(link))
        wt.review_begin(self.t, "s", TASK)
        wt.review_reset(self.t, "s", TASK)
        self.assertTrue(os.path.islink(link))

    def test_reset_undoes_everything_keeps_ignored(self):
        sha = wt.git(self.job, "rev-parse", "HEAD")
        os.makedirs(f"{self.job}/target")
        open(f"{self.job}/target/seed", "w").write("b")
        wt.review_begin(self.t, "s", TASK)
        open(f"{self.job}/f", "w").write("mutant")
        open(f"{self.job}/new.txt", "w").write("n")
        sh("-C", self.job, "add", "-A")
        sh("-C", self.job, "commit", "-qm", "sneaky")
        open(f"{self.job}/f", "w").write("again")
        self.assertEqual(wt.review_reset(self.t, "s", TASK), {"reset": True})
        self.assertEqual(wt.git(self.job, "rev-parse", "HEAD"), sha)
        self.assertEqual(wt.git(self.job, "status", "--porcelain"), "")
        self.assertEqual(open(f"{self.job}/f").read(), "x")
        self.assertFalse(os.path.exists(f"{self.job}/new.txt"))
        self.assertTrue(os.path.isfile(f"{self.job}/target/seed"))
        self.assertFalse(os.path.exists(wt.review_marker(self.t, "s", TASK)))
        self.assertEqual(wt.review_reset(self.t, "s", TASK), {"reset": False})  # idempotent

    def test_reset_fails_loudly_and_keeps_marker(self):
        wt.review_begin(self.t, "s", TASK)
        with unittest.mock.patch.object(worktree_merge, "_dirty", return_value=[" M f"]), self.assertRaisesRegex(RuntimeError, "failed"):
            wt.review_reset(self.t, "s", TASK)
        self.assertTrue(os.path.isfile(wt.review_marker(self.t, "s", TASK)))
        with unittest.mock.patch.object(worktree_merge, "_dirty", return_value=[" M f"]):
            self.assertIn("failed", worktrees.review_reset(self.a, TASK))
        wt.review_reset(self.t, "s", TASK)

    def test_recover_from_leftover_marker(self):
        wt.review_begin(self.t, "s", TASK)
        open(f"{self.job}/f", "w").write("crash")
        self.assertEqual(wt.review_pending(self.t, "s"), [TASK])
        self.assertEqual(worktrees.review_recover(self.a), [])
        self.assertEqual(open(f"{self.job}/f").read(), "x")
        self.assertEqual(wt.review_pending(self.t, "s"), [])

    def run_one(self, agent, mutate=None):
        c = SimpleNamespace(dir=self.t, stage="s", w=f"{self.t}/.work/s")
        seen = {}

        def fake(args, cwd, done_file=None, env_extra=None, pgid_file=None):
            seen["env"] = env_extra
            if mutate:
                mutate()
            raise OSError("crash")

        d = {"id": "r1", "agent": agent, "model": "haiku", "prompt": "p", "task": TASK}
        with (
            unittest.mock.patch.object(claude, "session", fake),
            unittest.mock.patch.object(driver, "orch", lambda *a: {}),
            self.assertRaises(OSError),
        ):
            driver.run_one(c, d)
        return seen

    def test_driver_resets_after_crash(self):
        sha = wt.git(self.job, "rev-parse", "HEAD")

        def mutate():
            open(f"{self.job}/f", "w").write("m")
            open(f"{self.job}/n", "w").write("n")
            sh("-C", self.job, "add", "-A")
            sh("-C", self.job, "commit", "-qm", "x")

        seen = self.run_one("reviewer", mutate)
        self.assertEqual(seen["env"][ENV_REVIEW_DIR], self.job)
        self.assertEqual(wt.git(self.job, "rev-parse", "HEAD"), sha)
        self.assertEqual(wt.git(self.job, "status", "--porcelain"), "")
        self.assertEqual(wt.review_pending(self.t, "s"), [])

    def test_failed_reset_overrides_written_report(self):  # review audit H3
        c = SimpleNamespace(dir=self.t, stage="s", w=f"{self.t}/.work/s")
        d = {"id": "r1", "agent": "reviewer", "model": "haiku", "prompt": "p", "task": TASK}
        report = driver.work_path(c.w, "report-r1.md", create=True)

        def fake(args, cwd, done_file=None, env_extra=None, pgid_file=None):
            open(report, "w").write("VERDICT: PASS\nlooks fine")
            return {"result": "x"}

        with (
            unittest.mock.patch.object(claude, "session", fake),
            unittest.mock.patch.object(driver, "orch", lambda *a: {}),
            unittest.mock.patch.object(driver, "review_reset", return_value="boom"),
        ):
            driver.run_one(c, d)
        text = open(report).read()
        self.assertTrue(text.startswith("VERDICT: BLOCKED"))
        self.assertIn("looks fine", text)
        wt.review_reset(self.t, "s", TASK)

    def test_dev_gets_no_env_no_marker(self):
        self.assertNotIn(ENV_REVIEW_DIR, self.run_one("dev")["env"])
        self.assertEqual(wt.review_pending(self.t, "s"), [])

    def test_session_env_passed_only_when_given(self):
        with unittest.mock.patch.object(claude.subprocess, "Popen", side_effect=OSError("stop")) as po:
            claude.run_claude([], self.t, 1, None, {ENV_REVIEW_DIR: "/r"})
            self.assertEqual(po.call_args.kwargs["env"][ENV_REVIEW_DIR], "/r")
            claude.run_claude([], self.t, 1)
            self.assertNotEqual(po.call_args.kwargs["env"].get(ENV_REVIEW_DIR), "/r")

    def test_brief_and_agent(self):
        self.assertIn("never commit", worktrees.review_note(self.a, TASK))
        text = open(os.path.join(os.path.dirname(TOOLS), "agents", "reviewer.md")).read()
        self.assertRegex(text, r"(?m)^tools: .*\bEdit\b")

    def test_dirty_goes_back_to_dev_then_reviewer_then_cap(self):
        task = {"id": TASK}
        open(f"{self.job}/f", "w").write("d")
        with unittest.mock.patch.dict(os.environ, {steps.ENV_DRY_NO_CCN: "1"}):
            d = steps._disp_reviewer(self.a, task, "a1", [])
            self.assertEqual(d["agent"], "dev")
            self.assertIn("f", open(d["prompt"].split("fix the findings in ")[1].split(" first")[0]).read())
            sh("-C", self.job, "checkout", "--", "f")
            self.assertEqual(steps._disp_reviewer(self.a, task, "a2", [])["agent"], "reviewer")
            open(f"{self.job}/f", "w").write("d")
            done = {"ev": "report", "role": "dev", "verdict": "DONE", "id": "a0", "file": "x"}
            disp = {"ev": "dispatch", "role": "dev", "model": "haiku", "id": "a0"}
            mine = [dict(disp, id=f"a{i}") for i in range(steps.MAX_TRIES)] + [done]
            with (
                unittest.mock.patch.object(steps, "prereview_on", return_value=False),
                unittest.mock.patch.object(steps, "testrun_on", return_value=False),
                unittest.mock.patch.object(steps, "qualityrun_on", return_value=False),
            ):
                r = steps.next_step(self.a, task, mine, "a9")
            assert r is not None
            self.assertEqual(r["action"], "ask_main", r)


if __name__ == "__main__":
    unittest.main()
