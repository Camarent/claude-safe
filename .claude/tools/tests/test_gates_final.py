"""`gates_final` (pipeline.json): gates that run only in the final test: never in a task's gates, at a wave merge or in the red-base preflight."""

import argparse
import os
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import merge_wave  # noqa: E402
from audit import final_check, taskgates  # noqa: E402
from pipeline import phases, plan_check  # noqa: E402
from pipeline.configuration import FINAL_GATES_KEY, MERGE_GATES_KEY, ConfigError, all_gates_of, final_gates_of, gates_of, project  # noqa: E402
from tests.test_gates_merge import Base  # noqa: E402


class FinalBase(Base):
    def setUp(self):
        super().setUp()
        self.final_marker = f"{self.t}/final-hits"
        self.cfg[FINAL_GATES_KEY] = [{"name": "heavy", "cmd": f"echo hit >> {self.final_marker}"}]
        self.write_cfg()

    def final_hits(self):
        return len(open(self.final_marker).read().splitlines()) if os.path.exists(self.final_marker) else 0


class ConfigTest(FinalBase):
    def test_parsed_like_gates_and_listed_last(self):
        cfg = project(self.t)
        self.assertEqual([g["name"] for g in final_gates_of(cfg)], ["heavy"])
        self.assertEqual([g["name"] for g in all_gates_of(cfg)], ["t", "slow", "heavy"])
        self.assertEqual([g["name"] for g in gates_of(cfg)], ["t"])

    def test_bad_entries_and_a_name_in_two_lists_are_config_errors(self):
        for bad, why in (
            ({FINAL_GATES_KEY: "x"}, "must be a list"),
            ({FINAL_GATES_KEY: [{"name": "a"}]}, "empty `cmd`"),
            ({FINAL_GATES_KEY: [{"name": "t", "cmd": "y"}]}, "`gates` and `gates_final`"),
            ({FINAL_GATES_KEY: [{"name": "slow", "cmd": "y"}]}, "`gates_merge` and `gates_final`"),
        ):
            self.cfg.update(bad)
            self.write_cfg()
            with self.assertRaises(ConfigError, msg=why) as c:
                project(self.t)
            self.assertIn(why, str(c.exception))
            self.setUp()

    def test_selection_by_where_the_gates_run(self):
        cfg = project(self.t)
        heavy = self.cfg[FINAL_GATES_KEY][0]["cmd"]
        self.assertNotIn(heavy, final_check.commands(cfg, self.m))
        self.assertNotIn(heavy, final_check.commands(cfg, self.m, merge=True))
        self.assertIn(heavy, final_check.commands(cfg, self.m, merge=True, final=True))
        self.assertEqual(final_check.gate_names(cfg, merge=True, final=True)[heavy], "heavy")
        self.assertNotIn(heavy, final_check.gate_names(cfg, merge=True))

    def test_the_merge_prompt_never_names_a_final_gate(self):
        cfg = project(self.t)
        self.assertNotIn("heavy", phases.gate_text(cfg, merge=True))
        self.assertIn("slow", phases.gate_text(cfg, merge=True))

    def test_a_project_with_only_final_gates_still_gets_the_script_final_step(self):
        self.cfg.pop("gates")
        self.cfg.pop(MERGE_GATES_KEY)
        self.write_cfg()
        a = argparse.Namespace(stage="s", dir=self.t, feature=None)
        step = phases._final_test_step(a, project(self.t), "task-1", "final-test", {"DONE"}, {"PASS"})
        self.assertEqual(step["agent"], "script")


class WhereTheyRunTest(FinalBase):
    def test_not_in_task_gates_not_at_the_merge_gate_once_in_the_final_test(self):
        self.job()
        v, text = taskgates.run("s", "task-1", self.t)
        self.assertEqual(v, "DONE", text)
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "DONE", text)
        self.assertEqual(self.final_hits(), 0)
        out = unittest.mock.MagicMock()
        with unittest.mock.patch("builtins.print", out):
            final_check.main(["--dir", self.t])
        self.assertEqual(self.final_hits(), 1)
        self.assertIn("VERDICT: DONE", out.call_args_list[0][0][0])

    def test_a_red_final_gate_fails_the_final_test_and_is_named(self):
        self.cfg[FINAL_GATES_KEY] = [{"name": "heavy", "cmd": "exit 3"}]
        self.write_cfg()
        out = unittest.mock.MagicMock()
        with unittest.mock.patch("builtins.print", out):
            final_check.main(["--dir", self.t])
        text = out.call_args_list[0][0][0]
        self.assertIn("VERDICT: FAIL", text)
        self.assertIn("gate heavy: FAILED", text)

    def test_the_preflight_does_not_run_it(self):
        from pipeline import branches

        a = argparse.Namespace(stage="s", dir=self.t, feature=None, dry_run=False)
        cfg = project(self.t)
        cmds = final_check.commands(cfg, self.m, merge=True)  # what branches._run_preflight asks for
        self.assertNotIn(self.cfg[FINAL_GATES_KEY][0]["cmd"], cmds)
        self.assertTrue(hasattr(branches, "_run_preflight") and a)


class PlanCheckTest(FinalBase):
    def test_a_bad_final_gate_command_is_a_plan_problem_named_with_its_key(self):
        self.cfg[FINAL_GATES_KEY] = [{"name": "heavy", "cmd": "bash scripts/missing-final.sh"}]
        self.write_cfg()
        probs = plan_check._gate_command_problems(argparse.Namespace(dir=self.t))
        self.assertTrue(any("gates_final `heavy`" in p for p in probs), probs)


if __name__ == "__main__":
    unittest.main()
