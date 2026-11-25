import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qrun
import wt
from pipeline import claude, driver
from pipeline.configuration import (
    ENV_AGENT_KIND,
    ENV_GATE_KIND,
    GATE_KIND_MUTATION,
    GATE_TIMEOUT_S,
    MUTATION_GATE_TIMEOUT_S,
)

TASK = "task-1"  # task id as data; asserts reference the constant (path-dependence gate reads assert literals)


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class MutationTimeoutTest(unittest.TestCase):
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
        sh("-C", self.m, "add", "f")
        sh("-C", self.m, "commit", "-qm", "i")
        open(f"{self.t}/.work/s/task-1.md", "w").write("Predicted paths: f")
        self.job = wt.create(self.t, "s", TASK)["root"]
        self.c = SimpleNamespace(dir=self.t, stage="s", w=f"{self.t}/.work/s")

    def run_one(self, agent):
        seen = {}

        def fake(args, cwd, done_file=None, env_extra=None, pgid_file=None):
            seen["env"] = env_extra
            seen["cwd"] = cwd
            raise OSError("crash")

        d = {"id": "r1", "agent": agent, "model": "haiku", "prompt": "p", "task": TASK}
        with (
            unittest.mock.patch.object(claude, "session", fake),
            unittest.mock.patch.object(driver, "orch", lambda *a: {}),
            self.assertRaises(OSError),
        ):
            driver.run_one(self.c, d)
        return seen

    def test_reviewer_session_marks_mutation_gate_kind(self):
        seen = self.run_one("reviewer")
        self.assertEqual(seen["env"][ENV_GATE_KIND], GATE_KIND_MUTATION)
        self.assertEqual(seen["env"][ENV_AGENT_KIND], "reviewer")

    def test_dev_session_has_no_gate_kind(self):
        self.assertNotIn(ENV_GATE_KIND, self.run_one("dev")["env"])

    def test_mutation_limit_is_below_the_full_gate_limit(self):
        self.assertGreater(MUTATION_GATE_TIMEOUT_S, 0)
        self.assertLess(MUTATION_GATE_TIMEOUT_S, GATE_TIMEOUT_S)

    def test_qrun_timeout_picks_mutation_limit_only_for_mutation_kind(self):
        env = {"ORCH_SCRIPT_TIMEOUT": str(GATE_TIMEOUT_S * 3)}
        with unittest.mock.patch.dict(os.environ, {**env, ENV_GATE_KIND: GATE_KIND_MUTATION}):
            self.assertEqual(qrun._timeout(), MUTATION_GATE_TIMEOUT_S)
        with unittest.mock.patch.dict(os.environ, env):
            os.environ.pop(ENV_GATE_KIND, None)
            self.assertEqual(qrun._timeout(), GATE_TIMEOUT_S)

    def test_session_cwd_is_job_worktree_for_dev_and_project_dir_for_reviewer(self):
        dev = {"id": "d1", "agent": "dev", "model": "haiku", "prompt": "p", "task": TASK}
        rev = {"id": "r1", "agent": "reviewer", "model": "haiku", "prompt": "p", "task": TASK}
        self.assertEqual(driver.session_cwd(self.c, dev), self.job)
        self.assertEqual(driver.session_cwd(self.c, rev), self.t)


if __name__ == "__main__":
    unittest.main()
