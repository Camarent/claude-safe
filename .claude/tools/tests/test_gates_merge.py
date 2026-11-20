"""`gates_merge` (pipeline.json): gates that run only at the merge gate and the final test, never in a task's job worktree."""

import argparse
import json
import os
import sys
import unittest
import unittest.mock
from typing import Any

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import merge_wave  # noqa: E402
import wt  # noqa: E402
from audit import final_check, taskgates  # noqa: E402
from pipeline import phases, plan_check  # noqa: E402
from pipeline.configuration import MERGE_GATES_KEY, ConfigError, all_gates_of, gates_of, merge_gates_of, project  # noqa: E402
from tests.support.gitrepo import git  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402

SLOW = {"name": "slow", "cmd": "echo hit >> {marker}"}


class Base(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        self.marker = f"{self.t}/hits"
        self.cfg: dict[str, Any] = {
            "repo": "main",
            "branch_work": "dev",
            "worktrees": True,
            "gates": [{"name": "t", "cmd": "true"}],
            MERGE_GATES_KEY: [{**SLOW, "cmd": SLOW["cmd"].format(marker=self.marker)}],
        }
        self.write_cfg()
        git(self.m, "init", "-q", "-b", "dev")
        with open(f"{self.m}/f", "w") as f:
            f.write("x")
        git(self.m, "add", "f")
        git(self.m, "commit", "-qm", "i")
        with open(f"{self.t}/.work/s/task-1.md", "w") as f:
            f.write("Predicted paths: src/x")

    def write_cfg(self):
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump(self.cfg, f)

    def job(self, task="task-1", name="a"):
        r = wt.create(self.t, "s", task)
        with open(f"{r['root']}/{name}", "w") as f:
            f.write(task)
        wt.commit(self.t, "s", task, f"feat(x): add {name}")

    def hits(self):
        return len(open(self.marker).read().splitlines()) if os.path.exists(self.marker) else 0


class ConfigTest(Base):
    def test_merge_gates_parse_like_gates_and_all_gates_lists_both(self):
        cfg = project(self.t)
        self.assertEqual([g["name"] for g in merge_gates_of(cfg)], ["slow"])
        self.assertEqual([g["name"] for g in all_gates_of(cfg)], ["t", "slow"])
        self.assertEqual([g["name"] for g in gates_of(cfg)], ["t"])  # task gates never see it

    def test_a_bad_entry_or_a_name_used_in_both_lists_is_a_config_error(self):
        for bad, why in (
            ({MERGE_GATES_KEY: "x"}, "must be a list"),
            ({MERGE_GATES_KEY: [{"name": "a"}]}, "empty `cmd`"),
            ({MERGE_GATES_KEY: [{"name": "t", "cmd": "y"}]}, "both"),
        ):
            self.cfg.update(bad)
            self.write_cfg()
            with self.assertRaises(ConfigError, msg=why) as c:
                project(self.t)
            self.assertIn(why, str(c.exception))
            self.setUp()

    def test_selection_by_where_the_gates_run(self):
        cfg = project(self.t)
        self.assertEqual(final_check.commands(cfg, self.m), ["true"])
        self.assertEqual(final_check.commands(cfg, self.m, merge=True), ["true", self.cfg[MERGE_GATES_KEY][0]["cmd"]])
        self.assertEqual(final_check.gate_names(cfg, merge=True)[self.cfg[MERGE_GATES_KEY][0]["cmd"]], "slow")
        self.assertNotIn(self.cfg[MERGE_GATES_KEY][0]["cmd"], final_check.gate_names(cfg))
        self.cfg[MERGE_GATES_KEY][0]["parallel"] = "g"
        self.write_cfg()
        self.assertEqual(final_check.gate_groups(project(self.t), merge=True), {self.cfg[MERGE_GATES_KEY][0]["cmd"]: "g"})
        self.assertEqual(final_check.gate_groups(project(self.t)), {})

    def test_the_merge_prompt_and_final_test_step_know_the_merge_gates_the_dev_prompt_does_not(self):
        cfg = project(self.t)
        self.assertNotIn("slow", phases.gate_text(cfg))
        self.assertIn("slow", phases.gate_text(cfg, merge=True))
        self.cfg["gates"] = []
        self.write_cfg()
        a = argparse.Namespace(dir=self.t, stage="s")
        step = phases._final_test_step(a, project(self.t), "task-1", "final-test", None, None)
        self.assertEqual(step["agent"], "script")  # gates_merge alone is enough for the script final test


class WhereTheyRunTest(Base):
    def test_zero_runs_in_task_gates_one_run_at_the_merge_gate(self):
        self.job()
        v, text = taskgates.run("s", "task-1", self.t)
        self.assertEqual(v, "DONE", text)
        self.assertEqual(self.hits(), 0)  # the task's gates did not run it
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "DONE", text)
        self.assertEqual(self.hits(), 1)
        recs = [r for r in map(json.loads, open(f"{self.t}/.work/s/metrics.jsonl", encoding="utf-8")) if r["ev"] == "gate_cmd"]
        self.assertEqual([(r["kind"], r["gate"]) for r in recs if r["gate"] == "slow"], [("merge", "slow")])
        self.assertEqual([r["kind"] for r in recs if r["gate"] == "t"], ["task", "merge"])

    def test_final_check_script_runs_it_too(self):
        out = unittest.mock.MagicMock()
        with unittest.mock.patch("builtins.print", out):
            final_check.main(["--dir", self.t])
        self.assertEqual(self.hits(), 1)
        self.assertIn("VERDICT: DONE", out.call_args_list[0][0][0])


class MergeRedTest(Base):
    """A red merge gate goes to the fix step; the base's red is never cleared (the red-base preflight blocks it before the run)."""

    def setUp(self):
        super().setUp()
        self.cfg[MERGE_GATES_KEY] = [{"name": "slow", "cmd": "exit 3"}]
        self.write_cfg()
        self.job()

    def test_red_merge_gate_goes_to_the_fix_step(self):
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "FAIL", text)
        self.assertIn("gate slow: FAILED", text)
        self.assertNotIn("red on the stage base too", text)

    def test_the_final_test_reports_the_red(self):
        out = unittest.mock.MagicMock()
        with unittest.mock.patch("builtins.print", out):
            final_check.main(["--dir", self.t, "--stage", "s"])
        self.assertIn("VERDICT: FAIL", out.call_args_list[0][0][0])


class PlanCheckTest(Base):
    def test_a_bad_merge_gate_command_is_a_plan_problem_named_with_its_key(self):
        self.cfg[MERGE_GATES_KEY] = [{"name": "slow", "cmd": "bash scripts/missing-premerge.sh"}]
        self.write_cfg()
        probs = plan_check._gate_command_problems(argparse.Namespace(dir=self.t))
        self.assertTrue(any("gates_merge `slow`" in p for p in probs), probs)
        self.cfg[MERGE_GATES_KEY] = [{"name": "slow", "cmd": "true"}]
        self.write_cfg()
        self.assertEqual(plan_check._gate_command_problems(argparse.Namespace(dir=self.t)), [])


class ReadersTest(Base):
    def test_ci_drift_and_prepare_checks_see_the_merge_gates(self):
        from pipeline import ci_drift, prepare_checks

        os.makedirs(f"{self.m}/.github/workflows")
        with open(f"{self.m}/.github/workflows/ci.yml", "w") as f:
            f.write("jobs:\n  t:\n    steps:\n      - run: slow-suite --all\n")
        self.cfg[MERGE_GATES_KEY] = [{"name": "slow", "cmd": "slow-suite --all"}]
        self.write_cfg()
        self.assertEqual(ci_drift.drift(self.t, project(self.t)), [])  # a CI command that is a merge gate is not drift
        self.cfg[MERGE_GATES_KEY] = [{"name": "slow", "cmd": "no-such-tool-xyz --x"}]
        self.write_cfg()
        self.assertTrue(any("gate `slow`" in p and "no-such-tool-xyz" in p for p in prepare_checks.toolchain_problems(self.t)))


if __name__ == "__main__":
    unittest.main()
