"""Characterization of pr_ops._clean_repo and _sweep branches (real temp git repos, gh faked)."""

import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_base, pr_ops
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


class CleanRepo(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.d = os.path.join(self.root, "w")
        os.makedirs(self.d)
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")
        self.a = SimpleNamespace(dir=self.d, stage="s", name="s")
        self.cfg = {}

    def clean(self, main=False):
        return pr_ops._clean_repo(self.a, self.d, self.cfg, "stage/s", main)

    def test_already_clean(self):
        self.assertEqual(self.clean(), "already clean")

    def test_unlanded_branch_blocks(self):
        git(self.d, "switch", "-q", "-c", "stage/s")
        with open(os.path.join(self.d, "f"), "w") as f:
            f.write("a")
        git(self.d, "add", "f")
        git(self.d, "commit", "-qm", "work")
        with self.assertRaises(pr_ops.Blocked):
            self.clean()

    def test_landed_branch_switches_and_deletes(self):
        git(self.d, "switch", "-q", "-c", "stage/s")
        self.assertEqual(self.clean(), "on master; deleted stage/s")

    def test_creates_release_from_origin(self):
        origin = os.path.join(self.root, "o.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", origin], check=True)
        git(self.d, "remote", "add", "origin", origin)
        git(self.d, "push", "-q", "origin", "master")
        git(self.d, "fetch", "-q", "origin")
        git(self.d, "switch", "-q", "-c", "other")
        git(self.d, "branch", "-D", "master")
        self.assertEqual(self.clean(), "created master from origin/master")


class Sweep(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")

    def sweep(self, table):
        real = pr_base._run

        def run(args, cwd=None, timeout=120):
            if args[0] == "gh":
                return table(args)
            return real(args, cwd, timeout)

        with mock.patch.object(pr_base, "_run", side_effect=run):
            return pr_ops._sweep(self.d, {})

    def test_list_failure_blocks(self):
        with self.assertRaisesRegex(pr_ops.Blocked, "gh pr list failed: nope"):
            self.sweep(lambda a: SimpleNamespace(returncode=1, stdout="", stderr="nope"))

    def test_merge_failure_and_fetch_failure_reported(self):
        prs = '[{"number": 1, "title": "t", "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}]}]'

        def table(a):
            if a[2] == "list":
                return SimpleNamespace(returncode=0, stdout=prs, stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="conflict")

        text = self.sweep(table)
        self.assertIn("#1 skipped (merge failed: conflict): t", text)
        self.assertIn("1 merge failed", text)
        self.assertIn("; fetch failed:", text)

    def test_bad_json_means_no_prs(self):
        text = self.sweep(lambda a: SimpleNamespace(returncode=0, stdout="not json", stderr=""))
        self.assertIn("summary: 0 Dependabot PR(s)", text)


BEHIND = "X Pull request head branch is not up to date with the base"
OPEN = {
    "gh pr view b --json state": SimpleNamespace(returncode=0, stdout='{"state": "OPEN"}', stderr=""),
    "gh pr view b --json isDraft": SimpleNamespace(returncode=0, stdout='{"isDraft": false}', stderr=""),
    "gh pr view b --json mergeable": SimpleNamespace(returncode=0, stdout='{"mergeable": "MERGEABLE"}', stderr=""),
    "gh pr view b --json headRefOid": SimpleNamespace(returncode=0, stdout='{"headRefOid": "sha-b"}', stderr=""),
    "gh run list": SimpleNamespace(returncode=0, stdout='[{"name": "ci", "status": "completed", "conclusion": "success"}]', stderr=""),
}


def fail(text):
    return SimpleNamespace(returncode=1, stdout="", stderr=text)


def ok():
    return SimpleNamespace(returncode=0, stdout="", stderr="")


class FakeClock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


class MergeBehindHead(unittest.TestCase):
    """_merge on a refused squash merge: only a 'head branch is not up to date' refusal runs update-branch, the checks gate and one retry."""

    def setUp(self):
        self.d = tracked_mkdtemp()

    def merge(self, merges, after_update=("sha-new",)):
        """after_update: the successive headRefOid answers once update-branch ran (the last one repeats); before it the head is sha-b."""
        self.calls: list[str] = []
        self.head_reads = 0
        updated = []

        def run(args, cwd=None, timeout=120):
            line = " ".join(args)
            self.calls.append(line)
            if line.startswith("gh pr merge"):
                return merges.pop(0)
            if line == "gh pr update-branch b":
                updated.append(True)
            if line == "gh pr view b --json headRefOid":
                if not updated:
                    return SimpleNamespace(returncode=0, stdout='{"headRefOid": "sha-b"}', stderr="")
                self.head_reads += 1
                sha = after_update[min(self.head_reads, len(after_update)) - 1]
                return SimpleNamespace(returncode=0, stdout=f'{{"headRefOid": "{sha}"}}', stderr="")
            for k, v in OPEN.items():
                if line.startswith(k):
                    return v
            return ok()

        with mock.patch.object(pr_base, "_run", side_effect=run):
            return pr_ops._merge(self.d, self.d, {}, "s", "b", FakeClock())

    def test_behind_head_updates_checks_then_retries_once(self):
        note = self.merge([fail(BEHIND), ok()])
        calls = self.calls
        self.assertIn("PR squash-merged", note)
        updates = [i for i, c in enumerate(calls) if c == "gh pr update-branch b"]
        checks = [i for i, c in enumerate(calls) if c.startswith("gh run list")]  # the checks gate reads the head's runs
        merges = [i for i, c in enumerate(calls) if c.startswith("gh pr merge b")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(len(merges), 2)
        self.assertTrue(merges[0] < updates[0] < checks[-1] < merges[1])

    def test_behind_match_is_case_insensitive(self):
        note = self.merge([fail(BEHIND.upper()), ok()])
        self.assertIn("PR squash-merged", note)
        self.assertEqual(self.calls.count("gh pr update-branch b"), 1)

    def test_second_refusal_blocks_with_both_outputs(self):
        with self.assertRaisesRegex(pr_ops.Blocked, "head branch is not up to date.*/ .*conflict again"):
            self.merge([fail(BEHIND), fail("conflict again")])

    def test_mutation_match_text_swapped_blocks_without_update(self):
        # mutation check: the update branch is driven by the named HEAD_BEHIND_TEXT only; with another text the same refusal must block
        with (
            mock.patch.object(pr_ops, "HEAD_BEHIND_TEXT", "a text gh never prints"),
            self.assertRaisesRegex(pr_ops.Blocked, "gh pr merge failed: "),
        ):
            self.merge([fail(BEHIND), ok()])
        self.assertFalse([c for c in self.calls if c.startswith("gh pr update-branch")])

    def test_other_merge_error_blocks_without_update(self):
        with self.assertRaisesRegex(pr_ops.Blocked, "gh pr merge failed: conflict"):
            self.merge([fail("conflict")])
        self.assertFalse([c for c in self.calls if c.startswith("gh pr update-branch")])

    def test_checks_gate_waits_until_head_moves(self):
        # the first headRefOid read after the update still shows sha-b: the checks must not be read before sha-new appears
        note = self.merge([fail(BEHIND), ok()], after_update=["sha-b", "sha-b", "sha-new"])
        self.assertIn("PR squash-merged", note)
        self.assertGreaterEqual(self.head_reads, 3)  # two stale reads, then the moved head
        update = self.calls.index("gh pr update-branch b")
        last_head = max(i for i, c in enumerate(self.calls) if c == "gh pr view b --json headRefOid")
        checks_after = [i for i, c in enumerate(self.calls) if c.startswith("gh run list") and i > update]
        self.assertTrue(update < last_head < checks_after[0])

    def test_head_that_never_moves_blocks_before_checks(self):
        with self.assertRaisesRegex(pr_ops.Blocked, "head of b did not move after update-branch"):
            self.merge([fail(BEHIND), ok()], after_update=["sha-b"])
        update = self.calls.index("gh pr update-branch b")
        self.assertFalse([c for c in self.calls[update:] if c.startswith("gh run list")])
        self.assertEqual(len([c for c in self.calls if c.startswith("gh pr merge")]), 1)


if __name__ == "__main__":
    unittest.main()
