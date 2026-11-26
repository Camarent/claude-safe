"""Synthetic tests for the scripted final verification. Contract only: what runs, what the verdict is, script vs tester choice."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import final_check as fc
from tests.support.orch_harness import OrchHarness
from tests.support.tmpdirs import tracked_mkdtemp


class FinalCheckTest(unittest.TestCase):
    def test_commands_from_gates_and_each(self):
        d = tracked_mkdtemp()
        for c in ("a", "b"):
            os.makedirs(f"{d}/courses/{c}")
            open(f"{d}/courses/{c}/course.json", "w").close()
        cfg = {
            "gates": [{"name": "build", "cmd": "make build"}, {"name": "test", "cmd": "make test"}],
            "each": [{"glob": "courses/*/course.json", "cmd": "tool check {dir}"}],
        }
        self.assertEqual(fc.commands(cfg, d), ["make build", "make test", "tool check courses/a", "tool check courses/b"])
        self.assertEqual(fc.commands({}, d), [])

    def test_each_dir_with_space_is_one_shell_word(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(f"{d}/courses/a b")
        open(f"{d}/courses/a b/course.json", "w").close()
        cfg = {"each": [{"glob": "courses/*/course.json", "cmd": "test -f {dir}/course.json"}]}
        cmds = fc.commands(cfg, d)
        self.assertEqual(cmds, ["test -f 'courses/a b'/course.json"])
        res = fc.run_all(cmds, d, dict(os.environ))
        self.assertEqual(res[0][1], 0, res[0][3])

    def test_timeout_and_oserror_become_failed_results(self):
        def boom(args, **kw):
            if "slow" in args[-1]:
                raise subprocess.TimeoutExpired(args, 1)
            raise OSError("no such file")

        res = fc.run_all(["slow", "fast"], tempfile.gettempdir(), dict(os.environ), runner=boom)
        self.assertEqual([r[1] for r in res], [124, 127])
        self.assertIn("timed out", res[0][3])
        self.assertIn("cannot run", res[1][3])
        self.assertTrue(fc.report(res).startswith("VERDICT: FAIL"))

    def test_run_all_runs_everything_and_report_puts_failures_first(self):
        d = tracked_mkdtemp()
        res = fc.run_all(["exit 0", "echo bad; exit 3", "exit 0"], d, dict(os.environ))
        self.assertEqual([r[1] for r in res], [0, 3, 0])  # a failure does not stop the rest
        rep = fc.report(res)
        self.assertTrue(rep.startswith("VERDICT: FAIL"))
        self.assertLess(rep.index("FAIL ("), rep.index("ok ("))
        self.assertTrue(fc.report([("x", 0, 0, "")]).startswith("VERDICT: DONE"))


class FinalStepChoice(OrchHarness):
    def run_to_final(self):
        self.answer(self.next(), "VERDICT: DONE")
        self.answer(self.next(), "VERDICT: PASS")  # dev, reviewer
        for _ in range(4):
            d = self.next()
            if d["agent"] == "tester" or "final_check.py" in d["prompt"]:
                return d
            self.answer(d, "VERDICT: DONE")
        return None

    def setUp(self):
        super().setUp()
        with open(f"{self.d}/.work/s/TASKS.md", "w") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\nflags: no-docs no-audit no-retro\n"
            )

    def test_script_when_gates_are_set_tester_otherwise(self):
        self.config(gates=[{"name": "t", "cmd": "make test"}])
        d = self.run_to_final()
        self.assertIsNotNone(d)
        self.assertEqual(d["agent"], "script")
        self.assertIn("final_check.py", d["prompt"])
        self.setUp()
        self.config()
        self.assertEqual(self.run_to_final()["agent"], "tester")


class FinalFixRoute(OrchHarness):
    """A red final check gets ONE fix round (architect -> normal tasks -> re-run), then the lead. Contract only."""

    def setUp(self):
        super().setUp()
        with open(f"{self.d}/.work/s/TASKS.md", "w") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\nflags: ui no-docs no-audit no-retro\n"
            )
        self.config(gates=[{"name": "t", "cmd": "make test"}])

    def until(self, pred, verdicts=None):
        for _ in range(12):
            d = self.next()
            if pred(d):
                return d
            self.answer(d, "VERDICT: " + (verdicts or {}).get(d["agent"], "PASS" if d["agent"] in ("reviewer", "tester", "web-tester") else "DONE"))
        self.fail("step not reached")

    def triage(self):
        """The final-test triage step after a red suite: red on the stage head only, so the fix round follows."""
        t = self.until(lambda d: d["agent"] == "script" and "steps_finaltest.py" in d["prompt"])
        self.answer(t, "VERDICT: FAIL\nred on the stage head only")

    def fix_round(self, check, report_word):
        a = self.until(lambda d: d["agent"] == "architect")
        self.assertIn(report_word, a["prompt"])
        self.assertIn("BLOCKED", a["prompt"])
        with open(f"{self.d}/.work/s/TASKS.md", "a") as f:
            f.write("| task-2 | fix | dev | 1 | wave 2, haiku |\nwave 2: task-2\n")
        self.answer(a, "VERDICT: DONE")

    def test_red_suite_gets_one_fix_round_then_the_lead(self):
        f = self.until(lambda d: d["agent"] == "script" and "final_check.py" in d["prompt"])
        self.answer(f, "VERDICT: FAIL\nFAIL make test")
        self.triage()
        self.fix_round("final-test", "report-" + f["id"])
        f2 = self.until(lambda d: d["agent"] == "script" and "final_check.py" in d["prompt"])
        self.answer(f2, "VERDICT: FAIL\nstill red")
        self.assertEqual(self.go("next")["action"], "ask_main")  # no second fix round

    def test_fixed_suite_moves_on_and_a_red_browser_check_gets_its_own_round(self):
        f = self.until(lambda d: d["agent"] == "script" and "final_check.py" in d["prompt"])
        self.answer(f, "VERDICT: FAIL\nred")
        self.triage()
        self.fix_round("final-test", f["id"])
        f2 = self.until(lambda d: d["agent"] == "script" and "final_check.py" in d["prompt"])
        self.answer(f2, "VERDICT: DONE")
        w = self.until(lambda d: d["agent"] == "web-tester")
        self.answer(w, "VERDICT: FAIL\nbutton missing")
        a = self.until(lambda d: d["agent"] == "architect")
        self.assertIn("report-" + w["id"], a["prompt"])
        with open(f"{self.d}/.work/s/TASKS.md", "a") as t:
            t.write("| task-3 | fix2 | dev | 1 | wave 3, haiku |\nwave 3: task-3\n")
        self.answer(a, "VERDICT: DONE")
        w2 = self.until(lambda d: d["agent"] == "web-tester")
        self.answer(w2, "VERDICT: PASS")
        r = self.go("next")
        while r.get("action") == "dispatch":  # plan-vs-changes (recall) runs after the checks
            for d in r["dispatches"]:
                self.answer(d, "VERDICT: DONE")
            r = self.go("next")
        self.assertIn("master-merged", r.get("message", ""))  # every check passed: the normal hand-over to the lead

    def test_architect_blocked_means_the_lead(self):
        f = self.until(lambda d: d["agent"] == "script" and "final_check.py" in d["prompt"])
        self.answer(f, "VERDICT: FAIL\ntoolchain missing")
        self.triage()
        a = self.until(lambda d: d["agent"] == "architect")
        self.answer(a, "VERDICT: BLOCKED")
        self.assertEqual(self.go("next")["action"], "ask_main")


if __name__ == "__main__":
    unittest.main()
