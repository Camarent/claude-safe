import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import final_check as fc
from pipeline.configuration import GATE_GROUP_MAX_WORKERS, ConfigError, each_of, gates_of


class ParallelGates(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name

    def run_(self, cmds, groups=None):
        return fc.run_all(cmds, self.d, dict(os.environ), groups=groups)

    @staticmethod
    def barrier(i, n=3):
        """A shell gate that only succeeds when all N barrier gates have started: it touches its own marker, then waits (bounded) until every marker exists. Run one after another, the first one can never see the others and fails."""
        marks = " && ".join(f"[ -e s{k} ]" for k in range(1, n + 1))
        return f"touch s{i}; n=0; while [ $n -lt 200 ] && ! {{ {marks}; }}; do n=$((n+1)); sleep 0.05; done; {marks}"

    def test_group_runs_concurrently_in_config_order(self):
        cmds = [self.barrier(1), "echo mid", self.barrier(2), self.barrier(3)]
        g = {cmds[0]: "js", cmds[2]: "js", cmds[3]: "js"}
        res = self.run_(cmds, g)
        self.assertEqual([r[0] for r in res], cmds)
        self.assertTrue(all(r[1] == 0 for r in res), [r[1] for r in res])  # a barrier gate passes only when the others ran at the same time

    def test_serial_without_groups(self):
        cmds = ["touch a; sleep 0.2; touch a.done", "test -e a.done"]  # the second passes only when the first has finished
        res = self.run_(cmds)
        self.assertEqual([r[0] for r in res], cmds)
        self.assertEqual([r[1] for r in res], [0, 0])

    def test_failing_gate_in_group_fails_and_later_still_runs(self):
        cmds = ["sleep 1", "echo bad; exit 3", "echo later"]
        g = {cmds[0]: "x", cmds[1]: "x"}
        names = {cmds[0]: "a", cmds[1]: "b"}
        res = self.run_(cmds, g)
        self.assertEqual([r[0] for r in res], cmds)
        self.assertEqual([r[1] for r in res], [0, 3, 0])
        self.assertIn("gate b: FAILED", fc.report(res, names))

    def _each_cfg(self, n, group="js", gates=None):
        for i in range(n):
            os.makedirs(f"{self.d}/w/c{i}")
            open(f"{self.d}/w/c{i}/course.json", "w").close()
        each = {"glob": "w/*/course.json", "cmd": "chk {dir}"}
        if group:
            each["parallel"] = group
        return {"gates": gates or [], "each": [each]}

    def _overlap_runner(self, target=1):
        """A fake runner that counts how many calls overlap. Every call is held until TARGET calls are inside at once (an Event, no sleep; a 10 s bound only keeps a broken scheduler from hanging the test), so the peak is exact."""
        lock, state = threading.Lock(), {"now": 0, "max": 0}
        reached = threading.Event()

        def runner(args, **kw):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
                if state["now"] >= target:
                    reached.set()
            reached.wait(10)
            with lock:
                state["now"] -= 1
            return subprocess.CompletedProcess(args, 0, "ok", "")

        return runner, state

    def test_each_group_runs_concurrently_in_config_order(self):
        cfg = self._each_cfg(3)
        cmds = fc.commands(cfg, self.d)
        runner, state = self._overlap_runner(3)
        res = fc.run_all(cmds, self.d, {}, runner=runner, groups=fc.gate_groups(cfg, self.d))
        self.assertEqual(state["max"], 3)
        self.assertEqual([r[0] for r in res], cmds)

    def test_each_joins_a_gate_group_of_the_same_name(self):
        cfg = self._each_cfg(2, gates=[{"name": "t", "cmd": "npm test", "parallel": "js"}])
        cmds = fc.commands(cfg, self.d)
        runner, state = self._overlap_runner(3)
        fc.run_all(cmds, self.d, {}, runner=runner, groups=fc.gate_groups(cfg, self.d))
        self.assertEqual(state["max"], 3)

    def test_group_bigger_than_cap_runs_in_waves(self):
        cfg = self._each_cfg(6)
        cmds = fc.commands(cfg, self.d)
        runner, state = self._overlap_runner(GATE_GROUP_MAX_WORKERS)
        res = fc.run_all(cmds, self.d, {}, runner=runner, groups=fc.gate_groups(cfg, self.d))
        self.assertEqual(state["max"], GATE_GROUP_MAX_WORKERS)
        self.assertEqual([r[0] for r in res], cmds)
        self.assertEqual(len(res), 6)

    def test_each_without_parallel_stays_sequential(self):
        cfg = self._each_cfg(3, group=None)
        cmds = fc.commands(cfg, self.d)
        self.assertEqual(fc.gate_groups(cfg, self.d), {})
        runner, state = self._overlap_runner()
        fc.run_all(cmds, self.d, {}, runner=runner, groups=fc.gate_groups(cfg, self.d))
        self.assertEqual(state["max"], 1)

    def test_each_parallel_must_be_a_string(self):
        with self.assertRaises(ConfigError):
            fc.commands({"each": [{"glob": "x", "cmd": "y", "parallel": 3}]}, self.d)
        self.assertEqual(each_of({"each": [{"glob": "x", "cmd": "y", "parallel": " "}]}), [{"glob": "x", "cmd": "y"}])

    def test_config_key_kept_and_validated(self):
        out = gates_of({"gates": [{"name": "a", "cmd": "x", "parallel": " js "}, {"name": "b", "cmd": "y"}]})
        self.assertEqual(out[0]["parallel"], "js")
        self.assertNotIn("parallel", out[1])
        with self.assertRaises(ConfigError):
            gates_of({"gates": [{"name": "a", "cmd": "x", "parallel": 3}]})


if __name__ == "__main__":
    unittest.main()
