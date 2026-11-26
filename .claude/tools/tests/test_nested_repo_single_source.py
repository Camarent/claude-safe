"""One definition of a nested git repo (worktree.nested_repos), one wave-conflict rule for plan check and `task --before`, and plan check reading each task once (synthetic data only)."""

import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph, plan_check, plan_files, plan_insert, plan_rules, worktree
from pipeline.configuration import WORK_DIR
from tests.support.tmpdirs import tracked_mkdtemp


def mkrepo(repo, rel, as_file=False):
    os.makedirs(os.path.join(repo, rel), exist_ok=True)
    if as_file:
        with open(os.path.join(repo, rel, ".git"), "w") as f:
            f.write("gitdir: elsewhere\n")
    else:
        os.makedirs(os.path.join(repo, rel, ".git"))


class NestedRepoAgrees(unittest.TestCase):
    def setUp(self):
        self.repo = tracked_mkdtemp()
        mkrepo(self.repo, "a/b/c")  # depth 3
        mkrepo(self.repo, "p", as_file=True)  # a worktree-style `.git` file
        mkrepo(self.repo, "w1/w2/w3/w4")  # depth 4: out of reach
        mkrepo(self.repo, worktree.SKIP[0] + "/x")  # inside a skipped dir

    def test_plan_rules_uses_the_worktree_definition(self):
        self.assertEqual(sorted(worktree.nested_repos(self.repo)), ["a/b/c", "p"])
        for path, want in [
            ("a/b/c/f.txt", "a/b/c"),
            ("a/b/c/d/e/f.txt", "a/b/c"),
            ("p/f", "p"),
            ("a/b/f", ""),
            ("top.txt", ""),
            ("w1/w2/w3/w4/f", ""),
            (f"{worktree.SKIP[0]}/x/f", ""),
        ]:
            with self.subTest(path=path):
                self.assertEqual(plan_rules.nested_repo(self.repo, path), want)

    def test_one_walk_per_call_chain(self):
        with mock.patch.object(worktree.os, "walk", wraps=os.walk) as walk:
            plan_rules._by_repo(self.repo, ["a/b/c/f", "p/g", "h", "p/i"])
        self.assertEqual(walk.call_count, 1)
        with mock.patch.object(worktree.os, "walk", wraps=os.walk) as walk:
            plan_rules.nested_repo(self.repo, "p/g", ["p"])  # the caller's list: no walk
        self.assertEqual(walk.call_count, 0)

    def test_dirty_uses_the_given_list(self):
        with mock.patch.object(worktree, "nested_repos", side_effect=AssertionError("walked")), mock.patch.object(worktree, "git", return_value=""):
            self.assertEqual(worktree._dirty(self.repo, self.repo, ["p"]), [])

    def test_has_job_branches_sees_a_depth_three_repo(self):
        root = tracked_mkdtemp()
        sub = os.path.join(root, "a", "b", "c")
        os.makedirs(sub)
        for r in (root, sub):
            subprocess.run(["git", "-C", r, "init", "-q", "-b", "dev"], check=True)
            subprocess.run(["git", "-C", r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        a = SimpleNamespace(dir=root, stage="s")
        with mock.patch.object(graph, "work_branch", return_value="dev"), mock.patch.object(graph, "project", return_value={"branch_release": "dev"}):
            self.assertFalse(graph._has_job_branches(a))
            subprocess.run(["git", "-C", sub, "branch", "job/s-task-1"], check=True)
            self.assertTrue(graph._has_job_branches(a))


class WaveConflictShared(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def write(self, n, paths):
        with open(os.path.join(self.d, f"task-{n}.md"), "w", encoding="utf-8") as f:
            f.write(f"# T{n}\nPredicted paths: {paths}\n")
        return {"id": f"task-{n}", "wave": 1}

    def insert(self, new_paths, old_paths):
        ents = [self.write(1, old_paths)]
        old = {e["id"]: plan_files.task_path_modes(self.d, e["id"]) for e in ents}
        plan_insert._check_overlap(ents, {"id": "task-9", "wave": 1}, plan_files.csv(new_paths), old)

    def test_insert_accepts_what_plan_check_accepts(self):
        self.insert("data/k.jsonl (rows)", "data/k.jsonl (rows)")
        ents = [self.write(1, "data/k.jsonl (rows)"), self.write(2, "data/k.jsonl (rows)")]
        self.assertEqual(plan_check._overlap_problems(self.d, ents), [])

    def test_insert_refuses_what_plan_check_refuses(self):
        for new, old in [
            ("data/k.jsonl (mass)", "data/k.jsonl (rows)"),
            ("data/k.jsonl", "data/k.jsonl (rows)"),
            ("data/k.json (rows)", "data/k.json (rows)"),
            ("a/x.py", "a/x.py"),
        ]:
            with self.subTest(new=new, old=old), self.assertRaises(plan_files.PlanError):
                self.insert(new, old)

    def test_other_wave_is_free(self):
        ents = [self.write(1, "a/x.py")]
        ents[0]["wave"] = 2
        self.assertIsNone(plan_insert._check_overlap(ents, {"id": "task-9", "wave": 1}, ["a/x.py"], {"task-1": plan_files.task_path_modes(self.d, "task-1")}))


class OverlapReadsOnce(unittest.TestCase):
    def test_each_task_file_is_read_once_and_pairs_stay_in_order(self):
        d = os.path.join(tracked_mkdtemp(), WORK_DIR, "s")
        os.makedirs(d)
        ents = []
        for n, (paths, wave) in enumerate([("a.py", 1), ("b.py", 1), ("a.py", 1), ("a.py", 2), ("b.py", 1)], 1):
            with open(os.path.join(d, f"task-{n}.md"), "w", encoding="utf-8") as f:
                f.write(f"Predicted paths: {paths}\n")
            ents.append({"id": f"task-{n}", "wave": wave})
        with mock.patch.object(plan_check, "task_path_modes", wraps=plan_files.task_path_modes) as read:
            probs = plan_check._overlap_problems(d, ents)
        self.assertEqual(read.call_count, len(ents))
        ids = [e["id"] for e in ents]
        self.assertEqual([p.split(" share")[0] for p in probs], [f"{ids[0]} and {ids[2]}", f"{ids[1]} and {ids[4]}"])


if __name__ == "__main__":
    unittest.main()
