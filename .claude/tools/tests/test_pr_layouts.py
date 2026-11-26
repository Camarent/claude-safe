"""PR steps and master-merged across repo layouts: single repo with/without origin, nested local repo inside a repo with origin."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import phases, pr_ops, tasks
from test_pr_steps import Base, git


class A:
    stage = "s"

    def __init__(self, d):
        self.dir = d


def final_ids(d):
    flags = {**tasks.parse_flags(os.path.join(d, "none.md")), "audit": False, "test": False, "index": False, "release": False}
    return [st["id"] for g in phases.final_phases(A(d), flags, [], []) for st in g]


class NoOriginTest(Base):
    def test_pr_steps_skip_without_origin(self):
        self.commit_on_stage()
        for what in ("open", "push", "ready", "merge"):
            v, text = pr_ops.run(what, self.d, "s", "x")
            self.assertEqual(v, "DONE", (what, text))
            self.assertIn("no origin", text)
        self.assertEqual([c for c in self.calls() if not c.startswith("pr view")], [])


class NestedTest(Base):
    def setUp(self):
        super().setUp()
        for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_EMAIL", "t@t")):
            os.environ[k] = v  # Base.tearDown restores os.environ
        self.add_origin()
        self.n = os.path.join(self.d, "course")
        os.makedirs(self.n)
        git(self.n, "init", "-q", "-b", "master")
        git(self.n, "commit", "-q", "--allow-empty", "-m", "seed")
        git(self.n, "switch", "-q", "-c", "stage/x")
        with open(os.path.join(self.n, "f.txt"), "w", encoding="utf-8") as f:
            f.write("work\n")
        git(self.n, "add", "f.txt")
        git(self.n, "commit", "-q", "-m", "course work")
        git(self.d, "switch", "-q", "-c", "stage/x")  # outer: no commit beyond master

    def test_ready_without_outer_work_is_not_blocked(self):
        v, text = pr_ops.run("ready", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        self.assertFalse([c for c in self.calls() if c.startswith("pr ready")])

    def test_merge_blocks_while_nested_has_unmerged_work(self):
        v, text = pr_ops.run("merge", self.d, "s", "x")
        self.assertEqual(v, "BLOCKED", text)
        self.assertIn("course", text)

    def test_merge_done_after_nested_local_merge(self):
        v, text = pr_ops.run("local-merge", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        v, text = pr_ops.run("merge", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)

    def test_local_merge_step_planned_for_nested_under_origin(self):
        self.assertIn("local-merge", final_ids(self.d))


if __name__ == "__main__":
    unittest.main()
