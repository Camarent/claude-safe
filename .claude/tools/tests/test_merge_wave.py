import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import merge_wave  # noqa: E402
import wt  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(*cmd):
    subprocess.run(["git", *cmd], check=True, capture_output=True)


class MergeWaveTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        self.cfg = {"repo": "main", "branch_work": "dev", "worktrees": True, "gates": [{"name": "t", "cmd": "true"}]}
        self._cfg()
        sh("-C", self.m, "init", "-q", "-b", "dev")
        with open(f"{self.m}/f", "w") as f:
            f.write("x")
        sh("-C", self.m, "add", "f")
        sh("-C", self.m, "commit", "-qm", "i")
        for n in ("task-1", "task-2"):
            with open(f"{self.t}/.work/s/{n}.md", "w") as f:
                f.write("Predicted paths: src/x")

    def _cfg(self):
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump(self.cfg, f)

    def _job(self, task, name):
        r = wt.create(self.t, "s", task)
        with open(f"{r['root']}/{name}", "w") as f:
            f.write(task)
        wt.commit(self.t, "s", task, f"feat(x): add {name}")

    def test_done_merges_tests_and_removes(self):
        self._job("task-1", "a")
        self._job("task-2", "b")
        v, text = merge_wave.run(self.t, "s", ["task-1", "task-2"])
        self.assertEqual(v, "DONE", text)
        self.assertTrue(os.path.isfile(f"{self.m}/a") and os.path.isfile(f"{self.m}/b"))
        self.assertFalse(os.path.exists(f"{self.t}/.wt/s/task-1"))

    def test_task_without_a_job_worktree_is_nothing_to_merge(self):  # a `Type: webtest` task has no dev and no worktree
        self._job("task-1", "a")
        v, text = merge_wave.run(self.t, "s", ["task-1", "task-30"])
        self.assertEqual(v, "DONE", text)
        self.assertTrue(os.path.isfile(f"{self.m}/a"))

    def test_stale_job_branch_is_rebased_before_merge(self):
        self._job("task-1", "a")
        with open(f"{self.m}/c", "w") as f:
            f.write("c")
        sh("-C", self.m, "add", "c")
        sh("-C", self.m, "commit", "-qm", "later")
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "DONE", text)
        self.assertIn("rebased onto the stage branch", text)

    def test_each_groups_reach_run_all(self):
        os.makedirs(f"{self.m}/w/c1")
        with open(f"{self.m}/w/c1/course.json", "w") as f:
            f.write("{}")
        self.cfg["each"] = [{"glob": "w/*/course.json", "cmd": "true {dir}", "parallel": "js"}]
        self._cfg()
        sh("-C", self.m, "add", "w")
        sh("-C", self.m, "commit", "-qm", "course")
        self._job("task-1", "a")
        seen = []
        real = merge_wave.final_check.run_all
        with unittest.mock.patch.object(merge_wave.final_check, "run_all", side_effect=lambda *a, **kw: seen.append(kw.get("groups")) or real(*a, **kw)):
            v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "DONE", text)
        self.assertEqual(seen, [{"true w/c1": "js"}])

    def test_refresh_stale_reports_nothing_when_current(self):
        self._job("task-1", "a")
        self.assertEqual(wt.refresh_stale(self.t, "s", ["task-1"]), ([], {}))

    def test_the_merge_gates_are_logged_per_command(self):
        self._job("task-1", "a")
        self._job("task-2", "b")
        self.cfg["gates"] = [{"name": "t", "cmd": "true"}, {"name": "u", "cmd": "false"}]
        self._cfg()
        merge_wave.run(self.t, "s", ["task-1", "task-2"])
        recs = [r for r in map(json.loads, open(f"{self.t}/.work/s/metrics.jsonl", encoding="utf-8")) if r["ev"] == "gate_cmd"]
        self.assertEqual(
            [(r["ev"], r["kind"], r["task"], r["gate"], r["rc"]) for r in recs],
            [("gate_cmd", "merge", "task-1,task-2", "t", 0), ("gate_cmd", "merge", "task-1,task-2", "u", 1)],
        )

    def test_red_tests_remove_merged_branches_and_rerun_is_idempotent(self):
        self._job("task-1", "a")
        self.cfg["gates"] = [{"name": "t", "cmd": "false"}]
        self._cfg()
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "FAIL", text)
        self.assertIn("task-1: removed (its content is in the stage branch)", text)
        self.assertFalse(os.path.isdir(f"{self.t}/.wt/s/task-1"))  # the work is on the stage branch: nothing left to block the driver
        self.assertTrue(os.path.isfile(f"{self.m}/a"))
        self.cfg["gates"] = [{"name": "t", "cmd": "true"}]
        self._cfg()
        self.assertEqual(merge_wave.run(self.t, "s", ["task-1"])[0], "DONE")

    def _warn_run(self, cmd, ok):
        self._job("task-1", "a")
        self.cfg["gates"] = [{"name": "val", "cmd": cmd}]
        self.cfg["gate_warn_ok"] = ok
        self._cfg()
        return merge_wave.run(self.t, "s", ["task-1"])

    def test_gate_warn_ok_accepts_a_warning_gate_of_that_stage_only(self):
        cmd = "echo 'WARNING: x'; exit 1"
        self.assertEqual(self._warn_run(cmd, {"other": ["val"]})[0], "FAIL")
        self.cfg["gate_warn_ok"] = {"s": ["val"]}
        self._cfg()
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "DONE", text)
        self.assertIn("gate val: red, accepted", text)
        self.assertIn("WARNING: x", text)

    def test_gate_warn_ok_refuses_plain_failure_star_and_bad_exit(self):
        self.assertEqual(self._warn_run("false", {"s": ["val"]})[0], "FAIL")
        self.cfg["gate_warn_ok"] = {"*": ["val"]}
        self.cfg["gates"] = [{"name": "val", "cmd": "echo warning; exit 1"}]
        self._cfg()
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "FAIL", text)
        self.assertIn("ignored", text)
        self.cfg["gates"] = [{"name": "val", "cmd": "echo warning; exit 127"}]
        self.cfg["gate_warn_ok"] = {"s": ["val"]}
        self._cfg()
        self.assertEqual(merge_wave.run(self.t, "s", ["task-1"])[0], "FAIL")

    def test_gate_warn_ok_explicit_exit_code_and_same_cmd_gates(self):
        self._job("task-1", "a")
        self.cfg["gates"] = [{"name": "a", "cmd": "exit 3"}, {"name": "b", "cmd": "exit 3"}]
        self.cfg["gate_warn_ok"] = {"s": [{"name": "a", "exit": [3]}]}
        self._cfg()
        v, text = merge_wave.run(self.t, "s", ["task-1"])
        self.assertEqual(v, "FAIL", text)
        self.assertIn("gate a: red, accepted", text)
        self.assertNotIn("gate b: red, accepted", text)

    def test_is_merged_needs_the_tree_in_the_work_branch(self):
        self._job("task-1", "g")
        self.assertFalse(wt.is_merged(self.t, "s", "task-1"))
        wt.mark_merged(self.t, "s", "task-1")
        self.assertFalse(wt.is_merged(self.t, "s", "task-1"))  # a marker alone is not trusted
        os.remove(os.path.join(f"{self.t}/.wt/s/task-1", ".merged"))  # merge skips a marked task
        wt.merge(self.t, "s", "task-1")
        self.assertTrue(wt.is_merged(self.t, "s", "task-1"))

    def test_conflict_fails_and_dirty_main_blocks(self):
        self._job("task-1", "f")
        with open(f"{self.m}/f", "w") as f:
            f.write("main")
        sh("-C", self.m, "commit", "-qam", "main side")
        self.assertEqual(merge_wave.run(self.t, "s", ["task-1"])[0], "FAIL")
        with open(f"{self.m}/f", "w") as f:
            f.write("dirty")
        self.assertEqual(merge_wave.run(self.t, "s", ["task-1"])[0], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
