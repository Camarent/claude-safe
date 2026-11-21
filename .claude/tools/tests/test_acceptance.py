"""Acceptance step: checklist parsing and plan_check rules, run() in a throwaway git repo with a fake runner, worktree cleanup, step order."""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import acceptance
from pipeline.configuration import ACCEPTANCE_FILE, DONE, FAIL, work_branch
from pipeline.plan_files import acceptance_items
from pipeline.plan_rules import acceptance_problems
from tests.support.gitrepo import git
from tests.support.project import dry_run, make_project
from tests.support.tmpdirs import tracked_mkdtemp

PLAN = "# Plan: f\n\n## Acceptance checklist\n- cmd: `true` (exit 0)\n- cmd: false\n- eyes: looks right\n\n## Phases\n"


def make_repo(plan):
    d = tracked_mkdtemp(prefix="accept-repo-")
    git(d, "init", "-q")
    with open(os.path.join(d, "f.txt"), "w") as f:
        f.write("x")
    git(d, "add", "f.txt")
    git(d, "commit", "-qm", "init")
    git(d, "branch", work_branch({}, "f"))
    os.makedirs(os.path.join(d, ".work", "f"))
    with open(os.path.join(d, ".work", "f", "Plan.md"), "w") as f:
        f.write(plan)
    return d


def worktrees(d):
    return subprocess.run(["git", "-C", d, "worktree", "list"], check=True, capture_output=True, text=True).stdout.strip().splitlines()


class RulesTest(unittest.TestCase):
    def test_items_strip_backticks_and_expected_result(self):
        self.assertEqual(acceptance_items(PLAN), [("cmd:", "true"), ("cmd:", "false"), ("eyes:", "looks right")])

    def test_bad_lines(self):
        txt = "## Acceptance checklist\n- do it\n- cmd: cd x\n- cmd: a && b\n- cmd: FOO=1 run\n- cmd:\n- cmd: ls /home/user/workspace/x\n- cmd: ok\n"
        probs = acceptance_problems(txt, "/home/user/workspace")
        self.assertEqual(len(probs), 7, probs)  # `ls /home/...` is both a main-checkout path and path-only

    def test_good_lines(self):
        self.assertEqual(acceptance_problems(PLAN), [])

    def test_prose_cmd_is_rejected(self):
        for prose in (
            "the suite passes with no failures",
            "All tests are green.",
            "Output should contain the files",
            "Verify the page loads",
            "Check that the build works",
            "The tests pass",
        ):
            probs = acceptance_problems(f"## Acceptance checklist\n- cmd: {prose}\n")
            self.assertEqual(len(probs), 1, (prose, probs))
            self.assertIn("sentence", probs[0])

    def test_real_commands_are_not_prose(self):
        # the verdict never depends on the host PATH
        with mock.patch.dict(os.environ, {"PATH": ""}):
            self._commands()
        self._commands()

    def _commands(self):
        for cmd in (
            "ls -la",
            "unknowntool run all",
            "python3 -I tools/check.py --all",
            "Rscript x",
            "Test-Path x",
            "Make Foo",
            "dotnet test the project",
            "npm run test all",
            "echo done",
            "xyzzy has passed",
        ):
            self.assertEqual(acceptance_problems(f"## Acceptance checklist\n- cmd: {cmd}\n"), [], cmd)


class RunTest(unittest.TestCase):
    def test_pass_fail_manual_and_cleanup(self):
        d = make_repo(PLAN)
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append((cmd, os.path.exists(cwd)))
            return (0, "ok") if cmd == "true" else (1, "boom\nline2")

        v, text = acceptance.run(d, "f", "f", runner)
        self.assertEqual(v, FAIL)
        self.assertEqual([c for c, _ in seen], ["true", "false"])
        self.assertTrue(all(e for _, e in seen))
        self.assertEqual(len(worktrees(d)), 1)
        self.assertIn("MANUAL", text)
        md = open(os.path.join(d, ".work", "f", ACCEPTANCE_FILE)).read()
        self.assertIn("FAIL exit 1: `false`", md)
        self.assertIn("PASS exit 0: `true`", md)

    def test_runner_exception_still_removes_worktree(self):
        d = make_repo(PLAN)

        def runner(cmd, cwd, timeout):
            raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            acceptance.run(d, "f", "f", runner)
        self.assertEqual(len(worktrees(d)), 1)

    def test_all_pass_is_done_and_missing_branch_fails(self):
        d = make_repo("## Acceptance checklist\n- cmd: true\n")
        self.assertEqual(acceptance.run(d, "f", "f", lambda c, w, t: (0, ""))[0], DONE)
        self.assertEqual(acceptance.run(d, "f", "nope", lambda c, w, t: (0, ""))[0], FAIL)
        self.assertEqual(len(worktrees(d)), 1)

    def test_seed_build_artifacts_are_copied_into_the_checkout(self):
        d = make_repo("## Acceptance checklist\n- cmd: seeded\n")
        os.makedirs(os.path.join(d, "target"))
        with open(os.path.join(d, "target", "a.bin"), "w") as f:
            f.write("b")
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append(os.path.isfile(os.path.join(cwd, "target", "a.bin")))
            return 0, ""

        with mock.patch.object(acceptance, "project", return_value={"seed_build": ["target/a.bin"]}):
            self.assertEqual(acceptance.run(d, "f", "f", runner)[0], DONE)
        self.assertEqual(seen, [True])

    def test_acceptance_prep_runs_first_and_a_failing_prep_fails(self):
        d = make_repo("## Acceptance checklist\n- cmd: main\n")
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append(cmd)
            return (1, "no build") if cmd == "prep" else (0, "")

        with mock.patch.object(acceptance, "project", return_value={"acceptance_prep": ["prep"]}):
            v, text = acceptance.run(d, "f", "f", runner)
        self.assertEqual(seen, ["prep", "main"])
        self.assertEqual(v, FAIL)
        self.assertIn("FAIL exit 1: `prep`", text)

    def test_nested_repo_with_the_branch_is_checked_out_and_removed(self):
        d = make_repo("## Acceptance checklist\n- cmd: nested\n")
        sub = os.path.join(d, "workspace", "course")
        os.makedirs(sub)
        git(sub, "init", "-q")
        with open(os.path.join(sub, "c.txt"), "w") as f:
            f.write("c")
        git(sub, "add", "c.txt")
        git(sub, "commit", "-qm", "init")
        git(sub, "branch", work_branch({}, "f"))
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append(os.path.isfile(os.path.join(cwd, "workspace", "course", "c.txt")))
            return 0, ""

        self.assertEqual(acceptance.run(d, "f", "f", runner)[0], DONE)
        self.assertEqual(seen, [True])
        self.assertEqual(len(worktrees(d)), 1)
        self.assertEqual(len(worktrees(sub)), 1)

    def test_nested_repo_without_the_branch_stays_absent(self):
        d = make_repo("## Acceptance checklist\n- cmd: nested\n")
        sub = os.path.join(d, "workspace", "course")
        os.makedirs(sub)
        git(sub, "init", "-q")
        with open(os.path.join(sub, "c.txt"), "w") as f:
            f.write("c")
        git(sub, "add", "c.txt")
        git(sub, "commit", "-qm", "init")
        seen = []
        acceptance.run(d, "f", "f", lambda c, w, t: seen.append(os.path.exists(os.path.join(w, "workspace", "course"))) or (0, ""))
        self.assertEqual(seen, [False])
        self.assertEqual(len(worktrees(sub)), 1)

    def test_private_course_is_linked_for_the_run_when_the_prefix_is_set(self):
        d = make_repo("## Acceptance checklist\n- cmd: nested\n")
        with open(os.path.join(d, "pipeline.json"), "w") as f:
            f.write('{"nested_repo_dir_prefix": "workspace/"}')
        with open(os.path.join(d, ".git", "info", "exclude"), "a") as f:
            f.write("workspace/*\n")  # a link stays only where git ignores it
        sub = os.path.join(d, "workspace", "course")
        os.makedirs(sub)
        git(sub, "init", "-q")
        seen = []
        acceptance.run(d, "f", "f", lambda c, w, t: seen.append(os.path.realpath(os.path.join(w, "workspace", "course"))) or (0, ""))
        self.assertEqual(seen, [os.path.realpath(sub)])
        self.assertTrue(os.path.isdir(sub))  # the main checkout's course stays


class SequenceTest(unittest.TestCase):
    def test_acceptance_sits_between_the_audits_and_the_merge(self):
        d = make_project("no-retro")
        os.makedirs(os.path.join(d, ".work", "s"), exist_ok=True)
        with open(os.path.join(d, ".work", "s", "Plan.md"), "w") as f:
            f.write(PLAN)
        _, steps, _ = dry_run(d)
        roles = [s[1] for s in steps]
        pos = [i for i, s in enumerate(steps) if "acceptance_step.py" in s[3]]
        self.assertEqual(len(pos), 1)
        self.assertGreater(pos[0], roles.index("tester"))
        self.assertGreater(pos[0], roles.index("arch-auditor"))

    def test_no_plan_no_step(self):
        _, steps, _ = dry_run(make_project("no-retro"))
        self.assertFalse(any("acceptance_step.py" in s[3] for s in steps))


if __name__ == "__main__":
    unittest.main()
