"""Session cwd and write-guard env per agent kind: dev runs in its job worktree, the other roles keep the project dir."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import driver
from pipeline.configuration import (
    ADD_DIR_FLAG,
    ENV_GUARD_ALLOW,
    ENV_GUARD_JOB,
    ENV_GUARD_LOG,
    ENV_GUARD_MAIN,
    ENV_GUARD_WORKTREE,
    GUARD_LOG_FILE,
    REPORTS_DIR,
    SCRATCH_ADD_DIR,
    SCRATCH_DIR,
    WORK_DIR,
)


class TestSessionCwd(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        with open(os.path.join(self.root, "pipeline.json"), "w") as f:  # worktree mode on for the project dir
            f.write('{"worktrees": true}')
        self.wt = os.path.join(self.root, "wt-task-1")
        self.c = SimpleNamespace(
            stage="s",
            dir=self.root,
            w=os.path.join(self.root, WORK_DIR, "s"),
            log=os.path.join(self.root, WORK_DIR, "s", "orch.jsonl"),
        )
        self.dev = {"id": "d1", "agent": "dev", "model": "", "prompt": "p", "task": "task-1"}

    def _job(self, root):
        """Patch job_info: a job worktree at `root`, or none when root is None."""
        info = None if root is None else {"root": root}
        return mock.patch.object(driver, "job_info", return_value=info)

    def test_dev_gets_worktree(self):
        with self._job(self.wt):
            self.assertEqual(driver.session_cwd(self.c, self.dev), self.wt)

    def test_other_agents_keep_dir(self):
        with self._job(self.wt):
            for agent in ("reviewer", "architect", "scout", "sec-auditor"):
                d = dict(self.dev, agent=agent)
                self.assertEqual(driver.session_cwd(self.c, d), self.root, agent)

    def test_no_worktree_mode_keeps_dir(self):
        with self._job(None):
            self.assertEqual(driver.session_cwd(self.c, self.dev), self.root)
            self.assertEqual(driver.guard_env(self.c, self.dev), {})

    def test_guard_env_binds_dev_to_worktree(self):
        with self._job(self.wt):
            env = driver.guard_env(self.c, self.dev)
        self.assertEqual(env[ENV_GUARD_WORKTREE], self.wt)
        self.assertEqual(env[ENV_GUARD_MAIN], os.path.abspath(self.root))
        self.assertEqual(env[ENV_GUARD_JOB], self.dev["task"])
        self.assertEqual(env[ENV_GUARD_LOG], os.path.join(self.c.w, GUARD_LOG_FILE))
        self.assertEqual(
            env[ENV_GUARD_ALLOW].split(os.pathsep),
            [os.path.join(self.c.w, REPORTS_DIR), os.path.join(self.c.w, SCRATCH_DIR)],
        )

    def _run_popen(self, d):
        """run_one of dispatch D with a fake Popen; -> (argv, kwargs) of the claude call."""
        calls = []
        real_popen = subprocess.Popen

        def fake_popen(argv, **kw):  # the claude call is faked; git calls of project() run for real
            if argv[0] != "claude":
                return real_popen(argv, **kw)
            calls.append((argv, kw))
            return SimpleNamespace(pid=1, returncode=0, communicate=lambda timeout=None: ('{"result": "ok", "is_error": false}', ""))

        with (
            mock.patch.object(driver.claude.subprocess, "Popen", side_effect=fake_popen),
            mock.patch.object(driver, "orch", return_value={}),
            mock.patch.object(driver, "review_begin", return_value=""),
            mock.patch.object(driver, "review_reset", return_value=""),
            mock.patch.object(driver, "review_root", return_value=self.wt),
            mock.patch.object(driver, "dispatch_task", return_value="task-1"),
            self._job(self.wt),
        ):
            driver.run_one(self.c, d)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_popen_cwd_per_agent(self):
        cases = {"dev": self.wt, "pl-dev": self.wt, "reviewer": self.root, "architect": self.root, "scout": self.root}
        for agent, cwd in cases.items():
            with self.subTest(agent=agent):
                _, kw = self._run_popen(dict(self.dev, agent=agent))
                self.assertEqual(kw["cwd"], cwd)

    def test_dev_argv_adds_work_and_scratch_dirs(self):
        argv, _ = self._run_popen(self.dev)
        pairs = [(argv[i], argv[i + 1]) for i in range(len(argv) - 1) if argv[i] == ADD_DIR_FLAG]
        self.assertIn((ADD_DIR_FLAG, os.path.join(self.root, WORK_DIR)), pairs)
        self.assertIn((ADD_DIR_FLAG, SCRATCH_ADD_DIR), pairs)

    def test_dev_env_has_guard(self):
        _, kw = self._run_popen(self.dev)
        self.assertEqual(kw["env"][ENV_GUARD_WORKTREE], self.wt)
        self.assertEqual(kw["env"][ENV_GUARD_JOB], self.dev["task"])


if __name__ == "__main__":
    unittest.main()
