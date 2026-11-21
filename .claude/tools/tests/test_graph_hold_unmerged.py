"""Dispatch-before-merge: a task whose dependency holds unmerged job work is held, and its job base is the work branch."""

import argparse
import json
import os
import subprocess
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wt
from pipeline import graph, worktrees
from pipeline.configuration import work_branch
from tests.support.tmpdirs import tracked_mkdtemp
from tests.test_graph_merge_units import Base

MEMBERS = [{"id": "task-3", "depends": []}, {"id": "task-4", "depends": ["task-3"]}]


class HoldUnmerged(Base):
    def setUp(self):
        super().setUp()
        self.job = {"task-3": {"branch": "job/task-3", "root": self.d, "repos": {}}}
        self.patch(graph, "job_info", side_effect=lambda _a, t, create: self.job.get(t) if not create else None)
        self.merged = self.patch(graph, "tasks_merged", return_value=False)

    def test_dependent_held_while_dep_job_unmerged(self):
        run, held = graph._hold(self.a, MEMBERS, set())
        self.assertEqual(held, {"task-4"})
        self.assertEqual([t["id"] for t in run], ["task-3"])

    def test_dependent_starts_once_dep_merged(self):
        self.merged.return_value = True
        run, held = graph._hold(self.a, MEMBERS, set())
        self.assertEqual(held, set())
        self.assertEqual([t["id"] for t in run], ["task-3", "task-4"])

    def test_dep_without_job_does_not_hold(self):
        self.job.clear()
        _run, held = graph._hold(self.a, MEMBERS, set())
        self.assertEqual(held, set())

    def test_job_base_is_work_branch(self):
        cfg = {"worktrees": True}
        self.patch(worktrees, "project", return_value=cfg)
        create = self.patch(worktrees.wt, "create", return_value={"branch": "job/task-4", "root": self.d, "repos": {}})
        info = worktrees.job_info(self.a, "task-4", create=True)
        create.assert_called_once_with(self.d, "s", "task-4")
        self.assertIsNotNone(info)
        self.assertEqual((info or {}).get("base"), work_branch(cfg, "s"))


def sh(*cmd):
    return subprocess.run(["git", *cmd], check=True, capture_output=True, text=True).stdout.strip()


class JobBaseHoldsMergedDep(unittest.TestCase):
    """Real git: task-4's job, created after task-3 merged, contains task-3's commit."""

    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump({"repo": "main", "branch_work": "dev", "worktrees": True}, f)
        sh("-C", self.m, "init", "-q", "-b", "dev")
        with open(f"{self.m}/f", "w") as f:
            f.write("x")
        sh("-C", self.m, "add", "f")
        sh("-C", self.m, "commit", "-qm", "i")
        for n in (3, 4):
            with open(f"{self.t}/.work/s/task-{n}.md", "w") as f:
                f.write("Predicted paths: g")

    def test_job_created_after_merge_has_dep_commit(self):
        a = argparse.Namespace(dir=self.t, stage="s", feature=None)
        root3 = wt.create(self.t, "s", "task-3")["root"]
        with open(f"{root3}/g", "w") as f:
            f.write("y")
        sh("-C", root3, "add", "g")
        sh("-C", root3, "commit", "-qm", "task-3 work")
        commit = sh("-C", root3, "rev-parse", "HEAD")
        wt.merge(self.t, "s", "task-3")
        info = worktrees.job_info(a, "task-4", create=True)
        self.assertEqual((info or {}).get("base"), "dev")
        sh("-C", (info or {})["root"], "merge-base", "--is-ancestor", commit, "HEAD")
        self.assertTrue(os.path.isfile(f"{(info or {})['root']}/g"))


if __name__ == "__main__":
    unittest.main()
