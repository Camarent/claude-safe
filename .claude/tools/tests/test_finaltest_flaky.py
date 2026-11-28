"""Final-test FAIL triage: the failing test files rerun alone, then on the preflight head (steps_finaltest.py)."""

import os
import sys
import unittest
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gate_record, phases, pr_ops, steps_finaltest
from pipeline.configuration import BLOCKED, DONE, FAIL, FINAL_FILES_CMD_KEY, FINALTEST_TRIAGE_STEP, PRE_EXISTING_RED
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp

REPORT = "VERDICT: FAIL\nFAILED .claude/tools/tests/test_alpha.py::test_one\nFAILED tests/test_beta.py::test_two\n"
CFG = {FINAL_FILES_CMD_KEY: "scripts/dev.sh test {files}"}


def runner_red_where(red_dirs):
    """A fake runner: exit 1 when the command runs in one of RED_DIRS, else 0; records the commands."""
    seen = []

    def run(argv, cwd, **_kw):
        seen.append((cwd, argv[-1]))
        return SimpleNamespace(returncode=1 if cwd in red_dirs else 0, stdout="", stderr="")

    return run, seen


class TriageUnitTest(unittest.TestCase):
    def test_test_files_are_named_in_order_once(self):
        self.assertEqual(
            steps_finaltest.test_files(REPORT + "FAILED tests/test_beta.py::test_two\n"),
            [".claude/tools/tests/test_alpha.py", "tests/test_beta.py"],
        )

    def test_rerun_cmd_quotes_the_files(self):
        self.assertEqual(steps_finaltest.rerun_cmd(CFG, ["a b/test_x.py"]), "scripts/dev.sh test 'a b/test_x.py'")

    def test_no_key_or_no_file_means_no_rerun(self):
        self.assertEqual(steps_finaltest.rerun_cmd({}, ["test_x.py"]), "")
        self.assertEqual(steps_finaltest.rerun_cmd(CFG, []), "")

    def test_no_rerun_command_goes_to_fix_without_running(self):
        run, seen = runner_red_where(set())
        verdict, _ = steps_finaltest.triage({}, "/repo", {}, REPORT, "abc", runner=run)
        self.assertEqual((verdict, seen), (FAIL, []))


class TriageRunTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "base")
        self.head = git(self.d, "rev-parse", "HEAD")
        self.worktrees_before = git(self.d, "worktree", "list")

    def triage(self, red_dirs, base_head=None):
        run, seen = runner_red_where(red_dirs)
        base = self.head if base_head is None else base_head
        return steps_finaltest.triage(CFG, self.d, {}, REPORT, base, runner=run), seen

    def test_green_alone_is_flaky(self):
        (verdict, reason), seen = self.triage(set())
        self.assertEqual(verdict, DONE)
        self.assertIn("flaky", reason)
        self.assertEqual(len(seen), 1)  # only the alone run: no base run, no fix

    def test_red_alone_without_preflight_head_goes_to_fix(self):
        (verdict, _), _seen = self.triage({self.d}, base_head="")
        self.assertEqual(verdict, FAIL)

    def test_red_only_on_stage_head_goes_to_fix(self):
        (verdict, reason), seen = self.triage({self.d})
        self.assertEqual(verdict, FAIL)
        self.assertIn("stage head only", reason)
        self.assertEqual(len(seen), 2)  # alone, then the base tree (green there: no base rerun)

    def test_red_on_preflight_head_too_is_pre_existing(self):
        seen = []

        def red_everywhere(argv, cwd, **_kw):  # red alone and red on the preflight head: the same failure in both trees
            seen.append(cwd)
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        verdict, reason = steps_finaltest.triage(CFG, self.d, {}, REPORT, self.head, runner=red_everywhere)
        self.assertEqual(len(seen), 3)  # the stage run, the base run, the base alone rerun: red all three times
        self.assertEqual(verdict, BLOCKED)
        self.assertIn(PRE_EXISTING_RED.replace("<command>", "scripts/dev.sh test"), reason)

    def test_green_alone_is_logged_as_a_flake_not_a_pass(self):
        self.triage(set())
        self.assertEqual(gate_record.flake_count(self.d), 1)
        self.assertIsNone(gate_record.find_passed(self.d, ["sh", "-c", "scripts/dev.sh test .claude/tools/tests/test_alpha.py tests/test_beta.py"]))

    def test_red_alone_is_not_logged_as_a_flake(self):
        self.triage({self.d})
        self.assertEqual(gate_record.flake_count(self.d), 0)

    def test_base_red_once_then_green_alone_is_not_pre_existing(self):
        calls = []

        def base_flaps(argv, cwd, **_kw):  # stage red alone; the base red on its first run, green on the alone rerun
            calls.append(cwd)
            base_runs = [c for c in calls if c != self.d]
            return SimpleNamespace(returncode=1 if cwd == self.d or len(base_runs) == 1 else 0, stdout="", stderr="")

        verdict, reason = steps_finaltest.triage(CFG, self.d, {}, REPORT, self.head, runner=base_flaps)
        self.assertEqual(verdict, FAIL)
        self.assertIn("stage head only", reason)
        self.assertEqual(gate_record.flake_count(self.d), 1)

    def test_base_tree_is_removed_after_the_run(self):
        base_dirs = []

        def record(argv, cwd, **_kw):
            if cwd != self.d:
                base_dirs.append(cwd)
            return SimpleNamespace(returncode=1 if cwd == self.d else 0, stdout="", stderr="")

        steps_finaltest.triage(CFG, self.d, {}, REPORT, self.head, runner=record)
        self.assertEqual(len(base_dirs), 1)
        self.assertFalse(os.path.exists(base_dirs[0]))
        self.assertEqual(git(self.d, "worktree", "list"), self.worktrees_before)


PR_GATE_OUT = "FAILED .claude/tools/tests/test_alpha.py::test_one\n"
PR_CFG = {"gates": [{"name": "test", "cmd": "scripts/dev.sh test"}], FINAL_FILES_CMD_KEY: "scripts/dev.sh test {files}"}


class PrReadyFlakeTest(unittest.TestCase):
    """pr-ready (pr_ops._gates_gate): a red gate whose failing files pass alone counts green and is logged as a flake."""

    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "base")

    def gate(self, red_cmds, rerun_red=False):
        """Runs the gate once with the fake: the gate line is red (stdout names one test file); the alone rerun is red when RERUN_RED."""
        seen = []

        def run(args, cwd, timeout):
            cmd = args[-1]
            seen.append(cmd)
            is_rerun = cmd.startswith("scripts/dev.sh test ")
            red = (cmd in red_cmds) or (is_rerun and rerun_red)
            return SimpleNamespace(returncode=1 if red else 0, stdout=PR_GATE_OUT if cmd in red_cmds else "", stderr="")

        return pr_ops._gates_gate(self.d, PR_CFG, "stage/x", runner=run), seen

    def test_red_gate_green_alone_counts_green_and_is_a_flake(self):
        note, seen = self.gate({"scripts/dev.sh test"})
        self.assertIn("1 command(s) green", note)
        self.assertEqual(seen, ["scripts/dev.sh test", "scripts/dev.sh test .claude/tools/tests/test_alpha.py"])
        self.assertEqual(gate_record.flake_count(self.d), 1)

    def test_red_gate_still_red_alone_blocks_and_is_not_a_flake(self):
        with self.assertRaises(pr_ops.Blocked) as cm:
            self.gate({"scripts/dev.sh test"}, rerun_red=True)
        self.assertIn("`scripts/dev.sh test`", str(cm.exception))
        self.assertEqual(gate_record.flake_count(self.d), 0)

    def test_red_gate_naming_no_file_is_not_rerun(self):
        with self.assertRaises(pr_ops.Blocked):
            pr_ops._gates_gate(self.d, PR_CFG, "stage/x", runner=lambda args, cwd, timeout: SimpleNamespace(returncode=1, stdout="", stderr=""))


class TriagePhasesTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q")
        self.a = SimpleNamespace(stage="s", dir=self.d, feature=None)
        self.cfg = {"gates": [{"name": "g", "cmd": "true"}]}
        self.branch = phases._wb(self.a, self.cfg)
        git(self.d, "commit", "-q", "--allow-empty", "-m", "one")
        git(self.d, "branch", "-f", self.branch)

    def ids(self, evs):
        return [s["id"] for g in phases._test_phases(self.a, self.cfg, {"ui": False}, [{"id": "task-1", "wave": 1}], evs) for s in g]

    def evs(self, triage_verdict=None):
        out = [
            {"ev": "report", "task": "final-test", "verdict": "FAIL", "file": "/r/ft.md"},
        ]
        if triage_verdict:
            out += [
                {"ev": "dispatch", "task": FINALTEST_TRIAGE_STEP},
                {"ev": "report", "task": FINALTEST_TRIAGE_STEP, "verdict": triage_verdict, "file": "/r/tr.md"},
            ]
        return out

    def test_triage_step_follows_a_failed_final_test(self):
        self.assertEqual(self.ids(self.evs()), ["final-test", FINALTEST_TRIAGE_STEP])

    def test_flaky_triage_skips_the_fix_plan(self):
        self.assertEqual(self.ids(self.evs(DONE)), ["final-test", FINALTEST_TRIAGE_STEP])

    def test_fail_triage_goes_to_fix_plan(self):
        self.assertEqual(self.ids(self.evs(FAIL)), ["final-test", FINALTEST_TRIAGE_STEP, "fix-plan-test", "final-test-2"])

    def test_pre_existing_red_stops_before_fix(self):
        self.assertEqual(self.ids(self.evs(BLOCKED)), ["final-test", FINALTEST_TRIAGE_STEP])


if __name__ == "__main__":
    unittest.main()
