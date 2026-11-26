import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from pipeline import briefs, linkguard  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402

ADD = "wt.py add-repo --stage s --task task-2 --repo"


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


def out(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class LinkGuardTest(unittest.TestCase):
    """task-2 does not list workspace/c: its job worktree links the main copy of that nested repo; task-1 lists it (a real worktree)."""

    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        self.c = f"{self.m}/workspace/c"
        os.makedirs(self.c)
        os.makedirs(f"{self.t}/.work/s")
        json.dump({"repo": "main", "branch_work": "dev", "nested_repo_dir_prefix": "workspace/"}, open(f"{self.t}/pipeline.json", "w"))
        for r in (self.m, self.c):
            sh("-C", r, "init", "-q", "-b", "dev")
            for name in ("f", "g"):
                open(f"{r}/{name}", "w").write("x")
            sh("-C", r, "add", "f", "g")
            sh("-C", r, "commit", "-qm", "i")
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write("workspace/*\n")  # a link stays only where git ignores it
        open(f"{self.t}/.work/s/task-1.md", "w").write("Predicted paths: workspace/c/f")
        open(f"{self.t}/.work/s/task-2.md", "w").write("Predicted paths: src/x")
        self.r = wt.create(self.t, "s", "task-2")
        self.link = f"{self.r['root']}/workspace/c"

    def check(self):
        return linkguard.check(self.t, "s", "task-2", ADD)

    def test_a_tracked_edit_after_the_snapshot_is_reverted_with_a_patch_and_named(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("edited through the link")
        note = self.check()
        self.assertIn("path outside this task's worktree", note)
        self.assertIn("workspace/c/f", note)
        self.assertIn(f"{ADD} workspace/c", note)  # the way out is named
        self.assertEqual(open(f"{self.c}/f").read(), "x")
        patch = note.split("kept as a patch: ")[1].split(" ")[0]
        self.assertIn("edited through the link", open(patch).read())
        subprocess.run(["git", "-C", self.c, "apply", patch], check=True)  # the patch applies back in that repo
        self.assertEqual(open(f"{self.c}/f").read(), "edited through the link")
        sh("-C", self.c, "checkout", "--", "f")
        self.assertEqual(self.check(), "")  # clean now: no loop

    def test_a_change_that_was_there_when_the_dev_started_is_not_the_devs(self):
        open(f"{self.c}/g", "w").write("lead edit")  # before the dev starts
        linkguard.snapshot(self.t, "s", "task-2")
        self.assertEqual(self.check(), "")
        self.assertEqual(open(f"{self.c}/g").read(), "lead edit")
        open(f"{self.link}/f", "w").write("dev edit")
        self.assertIn("workspace/c/f", self.check())
        self.assertEqual(open(f"{self.c}/g").read(), "lead edit")  # only the dev's file went back

    def test_new_untracked_files_are_reported_and_left_in_place_old_ones_are_not(self):
        open(f"{self.c}/old-leftover", "w").write("x")
        linkguard.snapshot(self.t, "s", "task-2")
        self.assertEqual(self.check(), "")
        open(f"{self.link}/created", "w").write("new")
        note = self.check()
        self.assertIn("new untracked files", note)
        self.assertIn("workspace/c/created", note)
        self.assertNotIn("old-leftover", note)
        self.assertTrue(os.path.exists(f"{self.c}/created"))  # never deleted: a test in another task may use it

    def test_a_commit_in_the_main_copy_is_reported_not_undone(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("committed through the link")
        sh("-C", self.c, "commit", "-qam", "by the dev")
        before = out(self.c, "rev-parse", "HEAD")
        note = self.check()
        self.assertIn("a commit was made", note)
        self.assertEqual(out(self.c, "rev-parse", "HEAD"), before)

    def test_a_staged_new_file_is_removed_and_does_not_loop(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/added", "w").write("new")
        sh("-C", self.c, "add", "added")
        self.assertIn("workspace/c/added", self.check())
        self.assertFalse(os.path.exists(f"{self.c}/added"))
        self.assertEqual(self.check(), "")

    def test_a_merge_in_progress_is_not_a_dev_write(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("squashed in")
        open(f"{self.c}/.git/SQUASH_MSG", "w").write("Squashed commit")
        self.assertEqual(self.check(), "")
        os.remove(f"{self.c}/.git/SQUASH_MSG")
        self.assertIn("workspace/c/f", self.check())

    def test_a_job_without_a_snapshot_gets_one_at_the_first_check_and_no_verdict(self):
        open(f"{self.c}/g", "w").write("lead edit, no snapshot exists")
        self.assertEqual(self.check(), "")  # nothing can be told apart: nothing is blamed, nothing reverted
        self.assertEqual(open(f"{self.c}/g").read(), "lead edit, no snapshot exists")
        open(f"{self.link}/f", "w").write("dev edit")
        self.assertIn("workspace/c/f", self.check())  # from now on the baseline holds
        self.assertEqual(open(f"{self.c}/g").read(), "lead edit, no snapshot exists")

    def test_a_diff_that_cannot_be_saved_leaves_the_file_and_says_so(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("edit")
        with unittest.mock.patch.object(linkguard, "_raw_diff", side_effect=subprocess.SubprocessError("git timed out")):
            note = self.check()
        self.assertIn("could not be saved", note)
        self.assertEqual(open(f"{self.c}/f").read(), "edit")  # still there: nothing was lost

    def test_crlf_and_non_utf8_files_are_saved_in_a_patch_that_restores_them(self):
        for name, data in (("crlf.txt", b"a\r\nb\r\n"), ("latin.txt", "caf\xe9\n".encode("latin-1"))):
            open(f"{self.c}/{name}", "wb").write(data)
            sh("-C", self.c, "add", name)
        sh("-C", self.c, "commit", "-qm", "more")
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/crlf.txt", "wb").write(b"a\r\nCHANGED\r\n")
        open(f"{self.link}/latin.txt", "wb").write("caf\xe9 changed\n".encode("latin-1"))
        note = self.check()
        self.assertIn("crlf.txt", note)
        self.assertEqual(open(f"{self.c}/crlf.txt", "rb").read(), b"a\r\nb\r\n")
        patch = note.split("kept as a patch: ")[1].split(" ")[0]
        subprocess.run(["git", "-C", self.c, "apply", patch], check=True)  # applies back byte for byte
        self.assertEqual(open(f"{self.c}/crlf.txt", "rb").read(), b"a\r\nCHANGED\r\n")
        self.assertEqual(open(f"{self.c}/latin.txt", "rb").read(), "caf\xe9 changed\n".encode("latin-1"))

    def test_names_with_quotes_and_non_ascii_characters_are_guarded(self):
        name = 'caf\u00e9 "q".txt'
        open(f"{self.c}/{name}", "w").write("x")
        sh("-C", self.c, "add", name)
        sh("-C", self.c, "commit", "-qm", "odd name")
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/{name}", "w").write("changed")
        open(f"{self.link}/new \u00fc.txt", "w").write("new")
        note = self.check()
        self.assertIn(name, note)
        self.assertIn("new \u00fc.txt", note)
        self.assertEqual(open(f"{self.c}/{name}").read(), "x")

    def test_a_second_snapshot_does_not_absorb_an_earlier_attempts_write(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("first attempt wrote this")
        linkguard.snapshot(self.t, "s", "task-2")  # a rework or Sonnet attempt builds its brief
        self.assertIn("workspace/c/f", self.check())

    def test_untracked_files_and_a_commit_are_reported_once(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/created", "w").write("new")
        open(f"{self.link}/f", "w").write("committed")
        sh("-C", self.c, "commit", "-qam", "by the dev")
        first = self.check()
        self.assertIn("new untracked files", first)
        self.assertIn("a commit was made", first)
        self.assertEqual(self.check(), "")  # the retry is not failed again for the same thing

    def test_a_corrupt_or_odd_baseline_is_no_baseline(self):
        base = os.path.join(os.path.dirname(self.r["root"]), "linkbase.json")
        for text in ("[]", "not json", '{"workspace/c": {"head": "x"}}', '{"workspace/c": []}'):
            open(base, "w").write(text)
            self.assertEqual(self.check(), "", text)  # no crash, a fresh baseline is taken
            self.assertIn("untracked", json.load(open(base))["workspace/c"])

    def test_removing_the_job_removes_its_snapshot(self):
        linkguard.snapshot(self.t, "s", "task-2")
        base = os.path.join(os.path.dirname(self.r["root"]), "linkbase.json")
        self.assertTrue(os.path.exists(base))
        wt.remove(self.t, "s", "task-2")
        self.assertFalse(os.path.exists(base))
        self.assertFalse(os.path.exists(os.path.dirname(self.r["root"])))  # and the job folder goes with it
        r = wt.create(self.t, "s", "task-2")  # the same id again: a fresh job
        open(f"{self.c}/g", "w").write("edit after the old job")
        linkguard.snapshot(self.t, "s", "task-2")
        self.assertEqual(linkguard.check(self.t, "s", "task-2", ADD), "")  # not blamed on the new dev
        self.assertTrue(r)

    def test_a_git_timeout_inside_the_revert_gives_a_note_not_a_crash(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("edit")
        with unittest.mock.patch.object(linkguard.gitx, "try_out", side_effect=subprocess.TimeoutExpired("git", 1)):
            self.check()  # no exception
        self.assertEqual(open(f"{self.c}/f").read(), "edit")  # nothing half-reverted without a word: the next check sees it again
        self.assertIn("workspace/c/f", self.check())

    def test_another_tasks_merge_moving_head_between_attempts_is_not_a_commit_by_the_dev(self):
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.c}/g", "w").write("merged by another task")
        sh("-C", self.c, "commit", "-qam", "merge of another task")
        linkguard.snapshot(self.t, "s", "task-2")  # the next attempt builds its brief
        self.assertEqual(self.check(), "")

    def test_a_file_name_that_is_a_glob_matches_only_itself(self):
        for name in ("a[1].txt", "a1.txt"):
            open(f"{self.c}/{name}", "w").write("x")
            sh("-C", self.c, "add", name)
        sh("-C", self.c, "commit", "-qm", "glob names")
        open(f"{self.c}/a1.txt", "w").write("lead edit")  # before the snapshot
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/a[1].txt", "w").write("dev edit")
        self.assertIn("a[1].txt", self.check())
        self.assertEqual(open(f"{self.c}/a[1].txt").read(), "x")
        self.assertEqual(open(f"{self.c}/a1.txt").read(), "lead edit")  # the pattern did not reach it

    def test_git_state_errors_leave_no_snapshot(self):
        real = linkguard.git

        def flaky(repo, *args, **kw):
            if args[:2] == ("rev-parse", "HEAD"):
                raise RuntimeError("git timed out")
            return real(repo, *args, **kw)

        with unittest.mock.patch.object(linkguard, "git", side_effect=flaky):
            linkguard.snapshot(self.t, "s", "task-2")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.r["root"]), "linkbase.json")))  # not a wrong baseline

    def test_one_repo_failing_does_not_drop_the_notes_of_another(self):
        d = f"{self.m}/workspace/d"
        os.makedirs(d)
        sh("-C", d, "init", "-q", "-b", "dev")
        open(f"{d}/f", "w").write("x")
        sh("-C", d, "add", "f")
        sh("-C", d, "commit", "-qm", "i")
        wt.create(self.t, "s", "task-2")  # links workspace/d too
        linkguard.snapshot(self.t, "s", "task-2")
        base = os.path.join(os.path.dirname(self.r["root"]), "linkbase.json")
        os.remove(base)
        linkguard.snapshot(self.t, "s", "task-2")
        open(f"{self.link}/f", "w").write("edit c")
        open(f"{d}/f", "w").write("edit d")
        real = linkguard.git

        def flaky(repo, *args, **kw):
            if repo == d and args[:1] == ("rev-parse",) and "HEAD" in args:
                raise RuntimeError("git timed out")
            return real(repo, *args, **kw)

        with unittest.mock.patch.object(linkguard, "git", side_effect=flaky):
            note = self.check()
        self.assertIn("workspace/c/f", note)
        self.assertEqual(open(f"{d}/f").read(), "edit d")  # the repo git could not answer for is left alone

    def test_git_that_cannot_answer_gives_no_verdict_and_no_crash(self):
        with unittest.mock.patch.object(linkguard, "git", side_effect=RuntimeError("git timed out")):
            linkguard.snapshot(self.t, "s", "task-2")
            self.assertEqual(self.check(), "")

    def test_a_task_that_lists_the_repo_is_not_guarded(self):
        r = wt.create(self.t, "s", "task-1")
        linkguard.snapshot(self.t, "s", "task-1")
        open(f"{r['repos']['workspace/c']}/f", "w").write("edit in its own worktree")
        self.assertEqual(linkguard.check(self.t, "s", "task-1", ADD), "")

    def test_the_dev_brief_takes_the_snapshot(self):
        a = unittest.mock.Mock(dir=self.t, stage="s", feature=None)
        with unittest.mock.patch.object(briefs, "job_info", return_value=self.r), unittest.mock.patch.object(briefs.linkguard, "snapshot") as snap:
            briefs._where(a, {"id": "task-2"})
        snap.assert_called_once_with(self.t, "s", "task-2")


if __name__ == "__main__":
    unittest.main()
