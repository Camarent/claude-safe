import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from pipeline import worktree  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402

# task ids as fixed values: the path-dependence gate flags literal `task-N` text inside an assert
T1, T2, T3, T4, T5, T6 = "task-1", "task-2", "task-3", "task-4", "task-5", "task-6"


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


def sh_out(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class WtTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(f"{self.m}/workspace/c")
        os.makedirs(f"{self.t}/.work/s")
        json.dump({"repo": "main", "branch_work": "dev", "nested_repo_dir_prefix": "workspace/"}, open(f"{self.t}/pipeline.json", "w"))
        for r in (self.m, f"{self.m}/workspace/c"):
            sh("-C", r, "init", "-q", "-b", "dev")
            open(f"{r}/f", "w").write("x")
            sh("-C", r, "add", "f")
            sh("-C", r, "commit", "-qm", "i")
        open(f"{self.t}/.work/s/task-1.md", "w").write("Predicted paths: workspace/c/f")
        open(f"{self.t}/.work/s/task-2.md", "w").write("Predicted paths: src/x")

    def test_nested_only_when_named_and_names_unique(self):
        sh("-C", self.m, "branch", "job/s-task-1")  # a leftover with the default name
        r = wt.create(self.t, "s", "task-1")
        self.assertEqual(r["branch"], f"job/s-{T1}-2")
        self.assertEqual(sorted(r["repos"]), ["main", "workspace/c"])
        self.assertTrue(os.path.isfile(f"{r['repos']['workspace/c']}/f"))
        self.assertEqual(wt.create(self.t, "s", T1)["branch"], f"job/s-{T1}-2")  # idempotent, stable
        self.assertEqual(list(wt.create(self.t, "s", T2)["repos"]), ["main"])
        wt.remove(self.t, "s", "task-1")
        self.assertFalse(os.path.exists(f"{self.t}/.wt/s/task-1"))
        self.assertNotIn(f"job/s-{T1}-2", subprocess.run(["git", "-C", self.m, "branch"], capture_output=True, text=True, check=False).stdout)

    def test_link_to_a_private_repo_becomes_a_real_worktree_once_the_task_names_it(self):
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write("workspace/*\n")  # a link stays only where git ignores it
        r = wt.create(self.t, "s", "task-2")
        link = f"{r['root']}/workspace/c"
        self.assertTrue(os.path.islink(link))
        self.assertEqual(list(r["repos"]), ["main"])  # a link is never recorded as a job worktree
        open(f"{self.t}/.work/s/task-2.md", "w").write("Predicted paths: workspace/c/f")  # the lead now names the nested repo
        r = wt.create(self.t, "s", "task-2")
        self.assertFalse(os.path.islink(link))
        self.assertTrue(os.path.isfile(f"{link}/.git"))  # a real linked worktree
        self.assertEqual(sorted(r["repos"]), ["main", "workspace/c"])

    def test_removal_ignores_edits_of_the_main_copy_seen_through_a_link(self):
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write("workspace/*\n")
        r = wt.create(self.t, "s", "task-2")
        self.assertTrue(os.path.islink(f"{r['root']}/workspace/c"))
        os.makedirs(f"{self.m}/workspace/c/ux")  # the owner's untracked work in the main copy of the private repo
        with open(f"{self.m}/workspace/c/ux/flows.json", "w") as f:
            f.write("[]")
        wt.remove(self.t, "s", "task-2")  # no refusal: the dirt is not this job's
        self.assertFalse(os.path.exists(f"{self.t}/.wt/s/task-2"))
        self.assertTrue(os.path.isfile(f"{self.m}/workspace/c/ux/flows.json"))  # and nothing of it was touched

    def test_a_main_checkout_that_is_itself_a_link_still_reports_its_dirt(self):
        real = f"{self.t}/real"
        os.makedirs(real)
        sh("-C", real, "init", "-q", "-b", "dev")
        open(f"{real}/u", "w").write("x")  # untracked
        link = f"{self.t}/link"
        os.symlink(real, link)
        self.assertTrue(wt._dirty(link, link))
        self.assertEqual(wt._dirty(link, link, skip_link=True), [])

    def test_refresh_never_touches_a_linked_main_repo(self):
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write("workspace/*\n")
        r = wt.create(self.t, "s", "task-2")
        open(f"{self.m}/workspace/c/precious", "w").write("keep")  # uncommitted work in the MAIN nested repo
        info = wt.read_manifest(self.t, "s")["task-2"]
        info["repos"]["workspace/c"] = f"{r['root']}/workspace/c"  # the corrupt manifest the reviewer reproduced
        worktree._write_manifest(self.t, "s", {"task-2": info})
        worktree.refresh(self.t, "s", "task-2", discard=True)
        self.assertTrue(os.path.isfile(f"{self.m}/workspace/c/precious"))

    def commit_in_job(self, root, name):
        open(f"{root}/{name}", "w").write(name)
        sh("-C", root, "add", name)
        sh("-C", root, "commit", "-qm", f"feat(x): add {name}")

    def test_merged_marker_stops_counting_when_the_job_branch_moves(self):
        r = wt.create(self.t, "s", "task-2")
        self.commit_in_job(r["root"], "one")
        wt.mark_merged(self.t, "s", "task-2")  # a hand merge of `one`
        self.assertTrue(worktree.marked_merged(self.t, "s", "task-2"))
        self.assertEqual(wt.merge(self.t, "s", T2), {"skipped": "marked merged"})
        self.commit_in_job(r["root"], "rework")  # the retry adds a commit after the hand merge
        self.assertFalse(worktree.marked_merged(self.t, "s", "task-2"))
        with self.assertRaises(RuntimeError):
            wt.remove(self.t, "s", "task-2")  # the unmerged rework is guarded again, not deleted
        self.assertTrue(os.path.isdir(r["root"]))
        self.assertNotEqual(wt.merge(self.t, "s", "task-2"), {"skipped": "marked merged"})

    def test_a_forced_remove_of_unmerged_work_leaves_an_event_and_a_plain_one_does_not(self):
        r = wt.create(self.t, "s", "task-2")
        self.commit_in_job(r["root"], "one")
        log = f"{self.t}/.work/s/orch.jsonl"
        wt.remove(self.t, "s", "task-2", force=True)  # unmerged: a discard
        evs = [json.loads(x) for x in open(log, encoding="utf-8")]
        self.assertEqual([(e["ev"], e["job"]) for e in evs], [("forced_remove", T2)])
        from pipeline import steps

        self.assertNotIn("task", evs[0])  # the retry ladder reads every event with a `task` and expects a `role` on its dispatches and reports
        self.assertEqual(steps.history(evs, T2), [])
        self.assertIn(f"refusing to remove {T2}", evs[0]["why"])
        r = wt.create(self.t, "s", "task-3")
        wt.remove(self.t, "s", "task-3", force=True)  # nothing to lose: no event
        self.assertEqual(len(open(log, encoding="utf-8").readlines()), 1)

    def test_a_task_cut_after_a_merge_has_the_merged_commit_and_one_cut_before_it_does_not(self):
        first = wt.create(self.t, "s", "task-2")
        self.commit_in_job(first["root"], "prereq.txt")
        open(f"{self.t}/.work/s/task-5.md", "w").write("Predicted paths: src/z")
        before = wt.create(self.t, "s", "task-5")  # cut from the stage branch while the prerequisite is still unmerged
        self.assertFalse(os.path.exists(f"{before['root']}/prereq.txt"))
        wt.remove(self.t, "s", "task-5")
        self.assertEqual(wt.merge(self.t, "s", T2), {".": "merged"})
        after = wt.create(self.t, "s", "task-5")  # the driver's late merge runs first, so a later task starts from this base
        self.assertTrue(os.path.isfile(f"{after['root']}/prereq.txt"))

    def test_a_torn_marker_is_stale_not_a_crash_and_the_refusal_says_so(self):
        r = wt.create(self.t, "s", "task-2")
        self.commit_in_job(r["root"], "one")
        marker = f"{os.path.dirname(r['root'])}/.merged"
        for text in ("{bad", "", "[1]", "null"):
            open(marker, "w").write(text)
            self.assertEqual(worktree.marker_state(self.t, "s", T2), "stale", text)
            self.assertFalse(worktree.marked_merged(self.t, "s", "task-2"))
        with self.assertRaises(RuntimeError) as ctx:
            wt.remove(self.t, "s", "task-2")
        self.assertIn("merged-marker=stale", str(ctx.exception))
        os.remove(marker)
        self.assertEqual(worktree.marker_state(self.t, "s", T2), "none")

    def test_the_marker_records_full_shas(self):
        r = wt.create(self.t, "s", "task-2")
        wt.mark_merged(self.t, "s", "task-2")
        heads = json.load(open(f"{os.path.dirname(r['root'])}/.merged"))
        self.assertTrue(all(len(h) == 40 for h in heads.values()), heads)

    def test_a_tag_named_like_the_job_branch_does_not_change_the_recorded_head(self):
        r = wt.create(self.t, "s", "task-2")
        self.commit_in_job(r["root"], "one")
        branch_head = sh_out(r["root"], "rev-parse", "HEAD")
        self.commit_in_job(r["root"], "two")  # the tag will sit on a later commit than the branch head recorded below
        sh("-C", r["root"], "reset", "-q", "--hard", branch_head)
        sh("-C", self.m, "tag", "-a", "-m", "t", r["branch"], sh_out(self.m, "rev-parse", "dev"))
        wt.mark_merged(self.t, "s", "task-2")
        heads = json.load(open(f"{os.path.dirname(r['root'])}/.merged"))
        self.assertEqual(heads["."], branch_head)

    def test_marker_head_comparison_needs_a_real_abbreviation(self):
        self.assertTrue(worktree._same_head("abcdef0", "abcdef0123456789"))
        self.assertFalse(worktree._same_head("a", "abcdef0123456789"))  # one hex digit matches a sixteenth of all shas
        self.assertFalse(worktree._same_head("abcdef0", ""))  # a missing branch has no head

    def test_a_marker_with_short_shas_still_matches_the_same_heads(self):
        r = wt.create(self.t, "s", "task-2")
        self.commit_in_job(r["root"], "one")
        wt.mark_merged(self.t, "s", "task-2")
        marker = f"{os.path.dirname(r['root'])}/.merged"
        heads = json.load(open(marker))
        json.dump({k: v[:7] for k, v in heads.items()}, open(marker, "w"))  # written before full shas were stored
        self.assertTrue(worktree.marked_merged(self.t, "s", "task-2"))
        self.commit_in_job(r["root"], "two")
        self.assertFalse(worktree.marked_merged(self.t, "s", "task-2"))

    def test_an_older_marker_without_heads_still_counts(self):
        r = wt.create(self.t, "s", "task-2")
        open(f"{os.path.dirname(r['root'])}/.merged", "w").write("1\n")
        self.assertTrue(worktree.marked_merged(self.t, "s", "task-2"))

    def test_named_plain_dir_in_done_text_is_not_a_repo(self):  # B1: a Done/Test path to data (no git repo) must not block the dev
        os.makedirs(f"{self.m}/workspace/data")
        open(f"{self.m}/workspace/data/export.csv", "w").write("x")
        open(f"{self.t}/.work/s/task-3.md", "w").write("Predicted paths: src/y\nDone: export written to workspace/data/export.csv\n")
        r = wt.create(self.t, "s", "task-3")
        self.assertEqual(sorted(r["repos"]), ["main"])

    def test_named_plain_dir_in_predicted_paths_still_blocks(self):
        os.makedirs(f"{self.m}/workspace/data")
        open(f"{self.t}/.work/s/task-4.md", "w").write("Predicted paths: workspace/data/export.csv\n")
        with self.assertRaises(RuntimeError):
            wt.create(self.t, "s", "task-4")

    def test_reused_worktree_catches_up_with_base_unless_it_has_own_work(self):
        a = wt.create(self.t, "s", "task-2")
        open(f"{self.m}/g", "w").write("y")
        sh("-C", self.m, "add", "g")
        sh("-C", self.m, "commit", "-qm", "base moves on")  # e.g. a wave merge landed on dev
        wt.create(self.t, "s", "task-2")
        self.assertTrue(os.path.isfile(f"{a['root']}/g"))  # clean ancestor: fast-forwarded
        open(f"{a['root']}/own", "w").write("z")
        sh("-C", a["root"], "add", "own")
        sh("-C", a["root"], "commit", "-qm", "own work")
        open(f"{self.m}/h", "w").write("y")
        sh("-C", self.m, "add", "h")
        sh("-C", self.m, "commit", "-qm", "base moves again")
        wt.create(self.t, "s", "task-2")
        self.assertFalse(os.path.exists(f"{a['root']}/h"))  # own commits: left alone

    def test_parallel_devs_distinct_and_clean_removes_all(self):
        a, b = wt.create(self.t, "s", "task-1"), wt.create(self.t, "s", "task-2")
        self.assertEqual(wt.read_manifest(self.t, "s")[T2], b)  # structure persisted
        self.assertNotEqual(a["root"], b["root"])
        self.assertNotEqual(a["branch"], b["branch"])
        open(f"{a['root']}/g", "w").write("1")  # one dev's edit is invisible to the other
        self.assertFalse(os.path.exists(f"{b['root']}/g"))
        self.assertEqual(wt.list_wt(self.t, "s"), [T1, T2])
        wt.remove(self.t, "s", "task-2")
        self.assertEqual(list(wt.read_manifest(self.t, "s")), [T1])
        wt.clean(self.t, "s")
        self.assertFalse(os.path.exists(f"{self.t}/.wt"))
        for r in (self.m, f"{self.m}/workspace/c"):
            self.assertNotIn("job/", subprocess.run(["git", "-C", r, "branch"], capture_output=True, text=True, check=False).stdout)
            self.assertNotIn(".wt", subprocess.run(["git", "-C", r, "worktree", "list"], capture_output=True, text=True, check=False).stdout.split("\n", 1)[-1])

    def test_remove_refuses_unmerged_nested_and_dirty_work_unless_forced(self):
        r = wt.create(self.t, "s", "task-1")
        sub = r["repos"]["workspace/c"]
        open(f"{sub}/new", "w").write("n")
        with self.assertRaises(RuntimeError) as c:  # uncommitted in the nested worktree
            wt.remove(self.t, "s", "task-1")
        self.assertIn("uncommitted", str(c.exception))
        sh("-C", sub, "add", "new")
        sh("-C", sub, "commit", "-qm", "n")
        with self.assertRaises(RuntimeError) as c:  # committed, but not merged into dev
            wt.remove(self.t, "s", "task-1")
        self.assertIn("not merged", str(c.exception))
        self.assertTrue(os.path.isdir(sub))  # nothing was removed
        sh("-C", f"{self.m}/workspace/c", "merge", "--squash", r["branch"])  # squash merge: no ancestry
        sh("-C", f"{self.m}/workspace/c", "commit", "-qm", "m")
        wt.remove(self.t, "s", "task-1")
        self.assertFalse(os.path.exists(f"{self.t}/.wt/s/task-1"))
        r2 = wt.create(self.t, "s", "task-2")
        open(f"{r2['root']}/u", "w").write("u")
        wt.remove(self.t, "s", "task-2", force=True)  # force discards

    def _log(self, repo):
        return subprocess.run(["git", "-C", repo, "log", "--format=%s", "-3"], capture_output=True, text=True, check=False).stdout.split("\n")

    def test_merge_blocked_by_dirty_main_checkout_names_the_files(self):
        r = wt.create(self.t, "s", "task-1")
        open(f"{r['root']}/m", "w").write("m")
        wt.commit(self.t, "s", "task-1", "feat(x): add m")
        open(f"{self.m}/stray.txt", "w").write("s")
        with self.assertRaises(wt.Blocked) as cm:
            wt.merge(self.t, "s", "task-1")
        self.assertIn("stray.txt", str(cm.exception))
        self.assertIn("stash -u", str(cm.exception))

    def test_dirty_report_before_a_pipeline_starts(self):
        self.assertEqual(wt.dirty_report(self.m), "")
        os.makedirs(f"{self.m}/.work/s", exist_ok=True)
        open(f"{self.m}/.work/s/TASKS.md", "w").write("x")  # the pipeline's own folders never count
        self.assertEqual(wt.dirty_report(self.m), "")
        open(f"{self.m}/stray.txt", "w").write("s")
        msg = wt.dirty_report(self.m)
        self.assertTrue(msg.startswith("blocked: uncommitted changes"))
        self.assertIn("stray.txt", msg)
        self.assertIn("--allow-dirty", msg)

    def _job_with_node_modules_link(self):
        open(f"{self.m}/.gitignore", "w").write("node_modules/\n")  # trailing slash: matches a directory, not a symlink
        sh("-C", self.m, "add", ".gitignore")
        sh("-C", self.m, "commit", "-qm", "ignore")
        os.makedirs(f"{self.m}/node_modules")
        return wt.create(self.t, "s", "task-2")["repos"]["main"]

    def _exclude_file(self, w):
        ex = sh_out(w, "rev-parse", "--git-path", "info/exclude")
        return ex if os.path.isabs(ex) else os.path.join(w, ex)

    def test_create_excludes_the_node_modules_link_idempotently(self):
        w = self._job_with_node_modules_link()
        self.assertTrue(os.path.islink(f"{w}/node_modules"))
        wt.create(self.t, "s", "task-2")
        self.assertEqual(open(self._exclude_file(w)).read().splitlines().count("node_modules"), 1)
        self.assertEqual(sh_out(w, "status", "--porcelain"), "")

    def test_commit_never_stages_the_node_modules_link(self):
        w = self._job_with_node_modules_link()
        open(f"{w}/new.txt", "w").write("x")
        wt.commit(self.t, "s", "task-2", "feat(x): add new")
        files = sh_out(w, "ls-tree", "-r", "--name-only", "HEAD")
        self.assertNotIn("node_modules", files)
        self.assertIn("new.txt", files)

    def test_commit_skips_a_link_even_when_the_exclude_entry_is_gone(self):
        w = self._job_with_node_modules_link()
        open(self._exclude_file(w), "w").write("")
        open(f"{w}/new.txt", "w").write("x")
        wt.commit(self.t, "s", "task-2", "feat(x): add new")
        self.assertNotIn("node_modules", sh_out(w, "ls-tree", "-r", "--name-only", "HEAD"))

    def test_node_modules_symlink_is_not_dirty_even_with_a_dir_ignore(self):
        open(f"{self.m}/.gitignore", "w").write("node_modules/\n")
        sh("-C", self.m, "add", ".gitignore")
        sh("-C", self.m, "commit", "-qm", "ignore")
        os.makedirs(f"{self.t}/real_nm")
        os.symlink(f"{self.t}/real_nm", f"{self.m}/node_modules")
        self.assertEqual(wt._dirty(self.m, self.m), [])

    def test_tracked_pipeline_file_as_first_dirty_line_is_not_blamed(self):
        """git() must keep the leading space of ` M path`: a stripped first line makes [3:] cut the path wrong."""
        os.makedirs(f"{self.m}/.work/s", exist_ok=True)
        open(f"{self.m}/.work/s/TASKS.md", "w").write("x")
        sh("-C", self.m, "add", ".work/s/TASKS.md")
        sh("-C", self.m, "commit", "-qm", "tasks")
        open(f"{self.m}/.work/s/TASKS.md", "w").write("changed")  # " M .work/s/TASKS.md" is the only dirty line
        self.assertEqual(wt.dirty_report(self.m), "")

    def test_dirty_report_checks_nested_repos_too(self):
        open(f"{self.m}/workspace/c/left.py", "w").write("x")
        msg = wt.dirty_report(self.m)
        self.assertIn("workspace/c: ?? left.py", msg)

    def test_dirty_report_with_stage_skips_untouched_nested_repo(self):
        open(f"{self.m}/workspace/c/left.py", "w").write("x")
        os.makedirs(f"{self.m}/.work/s", exist_ok=True)
        open(f"{self.m}/.work/s/task-1.md", "w").write("Predicted paths: `src/a.py`\n")
        self.assertEqual(wt.dirty_report(self.m, "s"), "")  # the stage never touches workspace/c
        open(f"{self.m}/.work/s/task-2.md", "w").write("Predicted paths: `workspace/c/x.py`\n")
        self.assertIn("workspace/c: ?? left.py", wt.dirty_report(self.m, "s"))  # touched: blocks again
        open(f"{self.m}/stray.txt", "w").write("s")
        os.remove(f"{self.m}/.work/s/task-2.md")
        self.assertIn("stray.txt", wt.dirty_report(self.m, "s"))  # the main repo always blocks

    def test_wave_run_and_plan_run_refuse_a_dirty_checkout(self):
        from pipeline import cli_plan, cli_wave

        open(f"{self.m}/stray.txt", "w").write("s")
        for main, argv in (
            (cli_wave.main, ["run", "--stage", "s", "--dir", self.m]),
            (cli_plan.main, ["run", "--feature", "f", "--prompt-file", "x", "--dir", self.m]),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(argv)
            self.assertEqual(rc, 4)
            self.assertIn("blocked: uncommitted changes", buf.getvalue())

    def test_diff_without_commits_and_cut_at_the_limit(self):
        r = wt.create(self.t, "s", "task-1")
        self.assertEqual(wt.diff(self.t, "s", T1), "(no committed changes on the job branch)")
        open(f"{r['root']}/big", "w").write("line\n" * 500)
        wt.commit(self.t, "s", "task-1", "feat(x): add big")
        d = wt.diff(self.t, "s", "task-1", limit=200)
        self.assertIn("[... cut at 200 characters; full diff: git -C ", d)
        self.assertNotIn("[... cut", wt.diff(self.t, "s", T1, limit=100000))

    def test_cli_commit_from_inside_the_job_worktree_commits(self):
        """An agent runs `wt.py commit` from its job worktree with the default --dir: the manifest lives at the stage root, not there (it printed `{}`)."""
        r = wt.create(self.t, "s", "task-2")
        open(f"{r['root']}/m", "w").write("m")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(r["root"])
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            wt.main(["commit", "--stage", "s", "--task", "task-2", "--subject", "feat(x): add m"])
        out = json.loads(buf.getvalue())
        self.assertEqual(list(out), ["."])
        self.assertTrue(all(out.values()), buf.getvalue())
        self.assertEqual(sh_out(r["root"], "log", "-1", "--format=%s"), "feat(x): add m")

    def test_cli_commit_unknown_task_says_so_on_stderr(self):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            wt.main(["commit", "--stage", "s", "--task", "task-9", "--subject", "feat(x): add m", "--dir", self.t])
        self.assertEqual(json.loads(buf.getvalue()), {})
        self.assertIn("nothing was committed", err.getvalue())

    def test_commit_merge_diff_two_repos_then_remove(self):
        r = wt.create(self.t, "s", "task-1")
        sub = r["repos"]["workspace/c"]
        open(f"{sub}/n", "w").write("n")
        open(f"{r['root']}/m", "w").write("m")
        with self.assertRaisesRegex(RuntimeError, "commit subject must look like"):
            wt.commit(self.t, "s", "task-1", "bad subject")
        with self.assertRaisesRegex(wt.Blocked, "uncommitted changes"):  # uncommitted job work is never merged
            wt.merge(self.t, "s", "task-1")
        out = wt.commit(self.t, "s", "task-1", "feat(x): add files")
        self.assertEqual(sorted(out), [".", "workspace/c"])
        self.assertTrue(all(out.values()))
        self.assertEqual(wt.commit(self.t, "s", T1, "feat(x): add files"), {".": None, "workspace/c": None})
        d = wt.diff(self.t, "s", "task-1")
        self.assertIn("repo workspace/c", d)
        self.assertIn("+n", d)
        self.assertIn("+m", d)
        self.assertEqual(wt.merge(self.t, "s", T1), {"workspace/c": "merged", ".": "merged"})
        patches = sorted(os.listdir(f"{self.t}/.work/s/patches"))  # the task's own diff survives the squash and the worktree removal
        self.assertEqual(patches, [f"{T1}.patch", f"{T1}__workspace_c.patch"])
        self.assertIn("+n", open(f"{self.t}/.work/s/patches/{T1}__workspace_c.patch").read())
        self.assertEqual(self._log(self.m)[0], "feat(x): add files")
        self.assertEqual(self._log(f"{self.m}/workspace/c")[0], "feat(x): add files")
        self.assertEqual(wt.merge(self.t, "s", T1), {"workspace/c": "nothing", ".": "nothing"})  # idempotent
        wt.remove(self.t, "s", "task-1")
        self.assertFalse(os.path.exists(f"{self.t}/.wt/s/task-1"))

    def _committed_job(self, task):
        r = wt.create(self.t, "s", task)
        open(f"{r['root']}/g", "w").write("g")
        wt.commit(self.t, "s", task, "feat(x): add g")

    def test_merge_restores_main_when_not_on_work_branch(self):  # the detailed checks live in test_main_restore.py
        self._committed_job("task-3")
        sh("-C", self.m, "checkout", "-q", "-b", "other")
        self.assertEqual(wt.merge(self.t, "s", T3)["."], "merged")  # the stage branch is restored, the merge goes on
        self.assertEqual(sh_out(self.m, "branch", "--show-current"), "dev")

    def test_merge_blocks_when_main_checkout_dirty(self):
        self._committed_job("task-3")
        open(f"{self.m}/f", "w").write("dirty")
        with self.assertRaisesRegex(wt.Blocked, "main checkout .* has uncommitted changes"):
            wt.merge(self.t, "s", "task-3")

    def test_merge_blocks_when_commit_refused(self):
        self._committed_job("task-3")
        hook = f"{self.m}/.git/hooks/pre-commit"
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        with self.assertRaisesRegex(wt.Blocked, "commit in .* refused"):
            wt.merge(self.t, "s", "task-3")

    def test_merge_conflict_resets_and_mark_merged_skips(self):
        r = wt.create(self.t, "s", "task-2")
        open(f"{r['root']}/f", "w").write("job")
        wt.commit(self.t, "s", "task-2", "fix(x): job side")
        open(f"{self.m}/f", "w").write("main")
        sh("-C", self.m, "commit", "-qam", "main side")
        with self.assertRaisesRegex(wt.Conflict, "conflict merging"):
            wt.merge(self.t, "s", "task-2")
        self.assertEqual(open(f"{self.m}/f").read(), "main")  # main checkout reset to its own state
        self.assertEqual(
            subprocess.run(["git", "-C", self.m, "status", "--porcelain"], capture_output=True, text=True, check=False).stdout.replace("?? workspace/\n", ""),
            "",
        )
        with self.assertRaisesRegex(RuntimeError, "refusing to remove"):
            wt.remove(self.t, "s", "task-2")  # unmerged: guarded
        wt.mark_merged(self.t, "s", "task-2")
        self.assertEqual(wt.merge(self.t, "s", T2), {"skipped": "marked merged"})
        wt.remove(self.t, "s", "task-2")  # the marker lifts the guard
        self.assertFalse(os.path.exists(f"{self.t}/.wt/s/task-2"))

    def test_rework_naming_a_plain_course_dir_keeps_the_dev_commits(self):
        """create() refuses a course dir without a repo; a rework must not force-delete the job branch and its commits on the way out."""
        os.makedirs(f"{self.m}/workspace/plain")
        r = wt.create(self.t, "s", "task-5")
        open(f"{r['root']}/keep", "w").write("k")
        wt.commit(self.t, "s", "task-5", "feat(x): keep this")
        open(f"{self.t}/.work/s/task-5.md", "w").write("Predicted paths: workspace/plain/x")
        with self.assertRaisesRegex(RuntimeError, "no git repo"):
            wt.create(self.t, "s", "task-5")
        self.assertIn(r["branch"], subprocess.run(["git", "-C", self.m, "branch"], capture_output=True, text=True, check=False).stdout)
        log = subprocess.run(["git", "-C", self.m, "log", "-1", "--format=%s", r["branch"]], capture_output=True, text=True, check=False)
        self.assertEqual(log.stdout.strip(), "feat(x): keep this")

    def test_commit_with_gitignored_nested_repo(self):
        with open(f"{self.m}/.gitignore", "w") as f:
            f.write("workspace/c\n")
        sh("-C", self.m, "add", ".gitignore")
        sh("-C", self.m, "commit", "-qm", "ignore")
        r = wt.create(self.t, "s", "task-1")
        with open(f"{r['repos']['workspace/c']}/n", "w") as f:
            f.write("n")
        with open(f"{r['root']}/m", "w") as f:
            f.write("m")
        out = wt.commit(self.t, "s", "task-1", "feat(x): add files")
        self.assertTrue(out["."] and out["workspace/c"])

    def test_named_nested_dir_ignored_when_prefix_unset(self):
        from pipeline.configuration import clear_cache

        json.dump({"repo": "main", "branch_work": "dev"}, open(f"{self.t}/pipeline.json", "w"))  # no nested_repo_dir_prefix: rule off
        clear_cache()
        os.makedirs(f"{self.m}/workspace/plain")
        open(f"{self.t}/.work/s/task-6.md", "w").write("Predicted paths: workspace/plain/x")
        self.assertNotIn("workspace/plain", wt.create(self.t, "s", T6)["repos"])

    def test_named_course_without_repo_raises_and_slash_prefix_matches(self):
        os.makedirs(f"{self.m}/workspace/plain")  # a course dir that is no git repo
        open(f"{self.t}/.work/s/task-3.md", "w").write("Predicted paths: workspace/plain/x")
        with self.assertRaisesRegex(RuntimeError, "no git repo"):
            wt.create(self.t, "s", "task-3")
        open(f"{self.t}/.work/s/task-4.md", "w").write("Predicted paths: main/workspace/c/f")
        self.assertIn("workspace/c", wt.create(self.t, "s", T4)["repos"])

    def _linked_task(self):
        """task-2 does not list workspace/c: its job worktree holds only a link to the main copy."""
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write("workspace/*\n")
        return wt.create(self.t, "s", "task-2")

    def test_add_repo_turns_the_link_into_a_worktree_the_manifest_knows(self):
        r = self._linked_task()
        link = f"{r['root']}/workspace/c"
        self.assertTrue(os.path.islink(link))
        info = wt.add_repo(self.t, "s", "task-2", "workspace/c")
        self.assertFalse(os.path.islink(link))
        self.assertEqual(sorted(info["repos"]), ["main", "workspace/c"])
        self.assertEqual(sorted(wt.read_manifest(self.t, "s")[T2]["repos"]), ["main", "workspace/c"])
        open(f"{link}/f", "w").write("changed")  # an edit in the worktree: commit covers it, the main copy stays clean
        self.assertTrue(wt.commit(self.t, "s", "task-2", "fix(c): change f")["workspace/c"])
        self.assertEqual(sh_out(f"{self.m}/workspace/c", "status", "--porcelain"), "")

    def test_add_repo_refuses_an_unknown_repo_and_a_task_without_worktree(self):
        self._linked_task()
        with self.assertRaises(wt.Blocked):
            wt.add_repo(self.t, "s", "task-2", "workspace/nope")
        with self.assertRaises(wt.Blocked):
            wt.add_repo(self.t, "s", "task-9", "workspace/c")

    def test_create_keeps_a_repo_added_by_add_repo(self):
        self._linked_task()
        wt.add_repo(self.t, "s", "task-2", "workspace/c")
        info = wt.create(self.t, "s", "task-2")  # a rework dispatches create again
        self.assertEqual(sorted(info["repos"]), ["main", "workspace/c"])
        self.assertEqual(sorted(wt.read_manifest(self.t, "s")[T2]["repos"]), ["main", "workspace/c"])

    def test_add_repo_failure_puts_the_link_back(self):
        r = self._linked_task()
        link = f"{r['root']}/workspace/c"
        with unittest.mock.patch.object(worktree, "_add", side_effect=RuntimeError("worktree add failed")), self.assertRaises(RuntimeError):
            wt.add_repo(self.t, "s", "task-2", "workspace/c")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(list(wt.read_manifest(self.t, "s")[T2]["repos"]), ["main"])

    def test_no_change_with_real_git(self):
        from audit import taskgates

        r = wt.create(self.t, "s", "task-1")  # lists workspace/c: two worktrees
        self.assertTrue(taskgates.no_change(self.t, "s", "task-1"))
        open(f"{r['repos']['workspace/c']}/new", "w").write("x")  # an untracked file in the nested worktree
        self.assertFalse(taskgates.no_change(self.t, "s", "task-1"))
        os.remove(f"{r['repos']['workspace/c']}/new")
        self.assertTrue(taskgates.no_change(self.t, "s", "task-1"))
        open(f"{r['root']}/f", "w").write("edit")
        wt.commit(self.t, "s", "task-1", "fix(m): edit f")  # committed: HEAD moved off the base
        self.assertFalse(taskgates.no_change(self.t, "s", "task-1"))

    def test_add_repo_command(self):
        self._linked_task()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wt.main(["add-repo", "--stage", "s", "--task", "task-2", "--repo", "workspace/c", "--dir", self.t])
        self.assertIn("workspace/c", json.loads(out.getvalue())["repos"])


class SeedBuild(unittest.TestCase):
    def test_copies_only_listed_files_and_keeps_mtime(self):
        with tempfile.TemporaryDirectory() as t:
            src, dst = f"{t}/src", f"{t}/dst"
            for rel in ("out/a.bin", "out/b.bin"):
                os.makedirs(os.path.dirname(f"{src}/{rel}"), exist_ok=True)
                open(f"{src}/{rel}", "w").write("x")
            os.utime(f"{src}/out/a.bin", (1000, 1000))
            wt._seed_build(src, dst, ["out/a.bin", "out/missing"])
            self.assertEqual(os.stat(f"{dst}/out/a.bin").st_mtime, 1000)
            self.assertFalse(os.path.exists(f"{dst}/out/b.bin"))
            wt._seed_build(src, f"{t}/none", [])
            self.assertFalse(os.path.exists(f"{t}/none"))


class UnmergedDeletion(unittest.TestCase):
    def test_file_deleted_on_both_sides_counts_as_merged(self):
        """A squash merge leaves the job branch non-ancestral; a file both sides deleted must not read as unmerged."""
        r = tracked_mkdtemp()
        sh("-C", r, "init", "-q", "-b", "stage")
        open(f"{r}/f", "w").write("x")
        sh("-C", r, "add", "f")
        sh("-C", r, "commit", "-qm", "base")
        sh("-C", r, "branch", "job")
        sh("-C", r, "checkout", "-q", "job")
        sh("-C", r, "rm", "-q", "f")
        sh("-C", r, "commit", "-qm", "job deletes f")
        sh("-C", r, "checkout", "-q", "stage")
        sh("-C", r, "rm", "-q", "f")
        sh("-C", r, "commit", "-qm", "squash: stage deletes f")
        self.assertEqual(wt._unmerged(r, "job", "stage"), "")

    def test_merged_task_stays_removable_after_a_later_task_changes_its_file(self):
        """Task 1 is squash-merged, then task 2 changes the same file on the work branch: task 1 is still merged."""
        r = tracked_mkdtemp()
        sh("-C", r, "init", "-q", "-b", "stage")
        open(f"{r}/f", "w").write("a\nm\nb\n")
        sh("-C", r, "add", "f")
        sh("-C", r, "commit", "-qm", "base")
        sh("-C", r, "branch", "job")
        sh("-C", r, "checkout", "-q", "job")
        open(f"{r}/f", "w").write("task1\nm\nb\n")
        sh("-C", r, "commit", "-qam", "task 1")
        sh("-C", r, "checkout", "-q", "stage")
        sh("-C", r, "merge", "--squash", "job")
        sh("-C", r, "commit", "-qm", "squash task 1")
        open(f"{r}/f", "w").write("task1\nm\ntask2\n")
        sh("-C", r, "commit", "-qam", "task 2")
        self.assertEqual(wt._unmerged(r, "job", "stage"), "")

    def test_no_common_ancestor_is_unmerged_not_safe(self):
        """Empty merge-base (unrelated histories): the branch's own work must not read as safe to delete."""
        r = tracked_mkdtemp()
        sh("-C", r, "init", "-q", "-b", "stage")
        open(f"{r}/f", "w").write("x")
        sh("-C", r, "add", "f")
        sh("-C", r, "commit", "-qm", "base")
        sh("-C", r, "checkout", "-q", "--orphan", "job")
        open(f"{r}/work", "w").write("unmerged work")
        sh("-C", r, "add", "work")
        sh("-C", r, "commit", "-qm", "job work")
        sh("-C", r, "checkout", "-q", "stage")
        self.assertNotEqual(wt._unmerged(r, "job", "stage"), "")


class GitTimeout(unittest.TestCase):
    def test_git_timeout_raises_a_clear_runtime_error(self):
        """A hung git (e.g. a nested-repo merge) must fail with a readable error, not a raw TimeoutExpired traceback."""
        t = tracked_mkdtemp()
        hung = unittest.mock.patch.object(wt.subprocess, "run", side_effect=subprocess.TimeoutExpired(["git", "merge"], 120))
        with hung, self.assertRaises(RuntimeError) as c:
            wt.git(t, "merge", "job")
        self.assertIn("timed out", str(c.exception))
        self.assertIn("merge", str(c.exception))

    def test_unmerged_check_timeout_raises_a_clear_runtime_error(self):
        """_unmerged runs merge-tree directly: a hung check must get the same clear RuntimeError as git()."""
        r = tracked_mkdtemp()
        sh("-C", r, "init", "-q", "-b", "stage")
        open(f"{r}/f", "w").write("x")
        sh("-C", r, "add", "f")
        sh("-C", r, "commit", "-qm", "base")
        sh("-C", r, "branch", "job")
        real = subprocess.run

        def hang_on_merge_tree(args, *a, **k):
            if "merge-tree" in args:
                raise subprocess.TimeoutExpired(args, 120)
            return real(args, *a, **k)

        with unittest.mock.patch.object(wt.subprocess, "run", side_effect=hang_on_merge_tree), self.assertRaises(RuntimeError) as c:
            wt._unmerged(r, "job", "stage")
        self.assertIn("timed out", str(c.exception))
        self.assertIn("merge-tree", str(c.exception))


if __name__ == "__main__":
    unittest.main()
