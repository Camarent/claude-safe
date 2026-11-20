import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_ops, tasks
from tests.support.tmpdirs import tracked_mkdtemp

_GIT_ROUTE: Any


def setUpModule():
    """git calls of pr_ops go through gitx; the fakes below stand in for pr_ops._run, so route gitx.run there (as ["git", ...])."""
    global _GIT_ROUTE
    _GIT_ROUTE = mock.patch.object(pr_ops.gitx, "run", lambda repo, *a, timeout=60: pr_ops._run(["git", *a], repo, timeout))
    _GIT_ROUTE.start()


def tearDownModule():
    _GIT_ROUTE.stop()


FAKE_HOOK = """#!/usr/bin/env bash
# fake release_script: version lives in VERSION and in COPY (a second file), like package.json plus a manifest
case "$1" in
  current) if [ -n "${2:-}" ]; then git show "$2:VERSION"; else cat VERSION; fi ;;
  set) for f in VERSION COPY; do if grep -q "$2" $f; then sed -i "s/$2/$3/" $f; echo $f; fi; done ;;
esac
"""


class Bump(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(pr_ops._bump("0.1.5", "patch"), "0.1.6")
        self.assertEqual(pr_ops._bump("0.1.5", "minor"), "0.2.0")
        self.assertEqual(pr_ops._bump("0.1.5", "major"), "1.0.0")

    def test_non_semver_is_blocked(self):
        with self.assertRaises(pr_ops.Blocked):
            pr_ops._bump("1.2", "patch")
        with self.assertRaises(pr_ops.Blocked):
            pr_ops._bump("1.2.3-rc1", "patch")

    def test_level_default_patch_and_owner_override(self):
        d = tracked_mkdtemp()
        os.makedirs(f"{d}/.work/s")
        self.assertEqual(pr_ops._release_level(d, "s"), "patch")
        Path(d, ".work/s/TASKS.md").write_text("goal: x\nrelease: Minor\n", encoding="utf-8")
        self.assertEqual(pr_ops._release_level(d, "s"), "minor")
        Path(d, ".work/s/TASKS.md").write_text("release: huge\n", encoding="utf-8")
        self.assertEqual(pr_ops._release_level(d, "s"), "patch")

    def test_level_is_highest_over_phases(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for n, t in (("03-x", "goal: x\nrelease: minor\n"), ("04-finish", "goal: y\n")):
            os.makedirs(f"{d}/.work/feat/phases/{n}")
            Path(d, f".work/feat/phases/{n}/TASKS.md").write_text(t, encoding="utf-8")
        self.assertEqual(pr_ops._release_level(d, "feat/phases/04-finish"), "minor")
        Path(d, ".work/feat/phases/04-finish/TASKS.md").write_text("release: major\n", encoding="utf-8")
        self.assertEqual(pr_ops._release_level(d, "feat/phases/04-finish"), "major")

    def test_rewrite_pins_only_for_workspace_version_targets(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)
        for n, t in (
            ("core", "[package]\nversion.workspace = true\n"),
            ("other", '[package]\nversion = "0.1.0"\n'),
            (
                "app",
                '[dependencies]\ncore = { version = "0.1.0", path = "../core" }\nother = { path = "../other", version = "0.1.0" }\nserde = { version = "0.1.0" }\n',
            ),
        ):
            os.makedirs(f"{d}/{n}")
            Path(d, n, "Cargo.toml").write_text(t, encoding="utf-8")
        subprocess.run(["git", "-C", d, "add", "-A"], check=True)
        self.assertEqual(pr_ops._rewrite_pins(d, {"pin_manifest": "Cargo.toml"}, "0.2.0"), ["app/Cargo.toml"])
        text = Path(d, "app/Cargo.toml").read_text(encoding="utf-8")
        self.assertIn('core = { version = "0.2.0", path = "../core" }', text)
        self.assertIn('other = { path = "../other", version = "0.1.0" }', text)
        self.assertIn('serde = { version = "0.1.0" }', text)
        self.assertEqual(pr_ops._rewrite_pins(d, {"pin_manifest": ""}, "0.3.0"), [])


class RepoCase(unittest.TestCase):
    """A real tmp git repo: master with hook.sh, VERSION, COPY; stage/s one commit ahead."""

    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.d = tracked_mkdtemp()
        self.cfg = {"release_script": "hook.sh", "branch_release": "master"}
        os.makedirs(f"{self.d}/.work/s")
        self.w("hook.sh", FAKE_HOOK)
        self.w("VERSION", "0.1.5\n")
        self.w("COPY", "v 0.1.5\n")
        self.git("init", "-q", "-b", "master")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("update-ref", "refs/remotes/origin/master", "HEAD")
        self.git("checkout", "-q", "-b", "stage/s")
        self.w("FEATURE", "work\n")  # the stage must add something to master, else the bump is skipped
        self.git("add", "-A")
        self.git("commit", "-qm", "feat: stage work")

    def w(self, n, t):
        Path(self.d, n).write_text(t, encoding="utf-8")

    def r(self, n):
        return Path(self.d, n).read_text(encoding="utf-8")

    def git(self, *a):
        subprocess.run(["git", "-C", self.d, *a], check=True, capture_output=True)

    def bump(self):
        return pr_ops._version_bump(self.d, self.d, self.cfg, "s", "stage/s")

    def rev(self, ref):
        return subprocess.run(["git", "-C", self.d, "rev-parse", ref], check=True, capture_output=True, text=True).stdout.strip()


class Hook(RepoCase):
    def test_adds_nothing_to_master_skips_the_bump(self):
        self.git("update-ref", "refs/heads/master", "stage/s")  # master already has the stage work
        self.assertIn("adds nothing to master: no version bump", self.bump())
        self.assertEqual((self.r("VERSION"), self.r("COPY")), ("0.1.5\n", "v 0.1.5\n"))

    def test_bump_commits_every_file_the_hook_reports(self):
        note = self.bump()
        self.assertIn("patch bump v0.1.5 -> v0.1.6 (VERSION, COPY)", note)
        self.assertEqual((self.r("VERSION"), self.r("COPY")), ("0.1.6\n", "v 0.1.6\n"))
        log = subprocess.run(["git", "-C", self.d, "log", "-1", "--format=%s"], check=False, capture_output=True, text=True).stdout
        self.assertEqual(log.strip(), "chore(release): v0.1.6")

    def test_owner_level_from_tasks_md(self):
        self.w(".work/s/TASKS.md", "release: minor\n")
        self.assertIn("minor bump v0.1.5 -> v0.2.0", self.bump())

    def test_goal_version_from_plan_header_is_the_target(self):
        self.w(".work/s/Plan.md", "# Plan\n\nRelease dojo-kit v0.3.0.\n")
        self.assertIn("goal bump v0.1.5 -> v0.3.0", self.bump())
        self.assertEqual(self.r("VERSION"), "0.3.0\n")

    def test_version_already_at_goal_gets_no_second_bump(self):
        self.w(".work/s/Plan.md", "# Plan\n\nRelease dojo-kit v0.3.0.\n")
        self.w("VERSION", "0.3.0\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "phase bump")
        self.assertIn("no second bump", self.bump())
        self.assertEqual(self.r("VERSION"), "0.3.0\n")

    def test_version_text_deep_in_plan_is_not_a_goal(self):
        self.w(".work/s/Plan.md", "\n".join(["x"] * 20) + "\nsee v9.9.9\n")
        self.assertIn("patch bump v0.1.5 -> v0.1.6", self.bump())

    def test_partial_bump_is_completed_then_idempotent(self):
        self.w("VERSION", "0.1.6\n")  # the first bump missed COPY
        self.git("commit", "-qam", "chore(release): v0.1.6")
        self.assertIn("completed v0.1.5 -> v0.1.6 (COPY)", self.bump())
        self.assertEqual(self.r("COPY"), "v 0.1.6\n")
        self.assertIn("already at v0.1.6", self.bump())

    def test_missing_script_blocks_with_a_pointer(self):
        with self.assertRaises(pr_ops.Blocked) as e:
            pr_ops._hook(self.d, {"release_script": "nope.sh"}, "current")
        self.assertIn("no-release", str(e.exception))
        with self.assertRaises(pr_ops.Blocked):
            pr_ops._hook(self.d, {}, "current")

    def test_dirty_tree_blocks(self):
        self.w("VERSION", "0.1.5 \n")
        with self.assertRaises(pr_ops.Blocked):
            self.bump()


RELEASE_NOTES = "## [0.1.5] - 2026-11-05\n\n- fixed the thing\n\n## [0.1.4] - 2026-10-01\n\n- old\n"


def R(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class PinsThroughBump(RepoCase):
    """`_rewrite_pins` reached through a full `_version_bump` run: the pinned manifest joins the bump commit."""

    def test_version_bump_rewrites_pins_into_the_bump_commit(self):
        os.makedirs(f"{self.d}/core")
        os.makedirs(f"{self.d}/app")
        self.w("core/Cargo.toml", "[package]\nversion.workspace = true\n")
        self.w("app/Cargo.toml", '[dependencies]\ncore = { version = "0.1.5", path = "../core" }\nserde = { version = "0.1.5" }\n')
        self.git("add", "-A")
        self.git("commit", "-qm", "feat: manifests")
        note = self.bump()
        self.assertIn("patch bump v0.1.5 -> v0.1.6 (VERSION, COPY, app/Cargo.toml)", note)
        self.assertIn('core = { version = "0.1.6", path = "../core" }', self.r("app/Cargo.toml"))
        self.assertIn('serde = { version = "0.1.5" }', self.r("app/Cargo.toml"))
        files = subprocess.run(["git", "-C", self.d, "show", "--name-only", "--format=", "HEAD"], check=True, capture_output=True, text=True).stdout
        self.assertEqual(sorted(files.split()), ["COPY", "VERSION", "app/Cargo.toml"])
        self.assertEqual(subprocess.run(["git", "-C", self.d, "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip(), "")


class ReleaseReal(RepoCase):
    """_release on a real repo; only gh and `git push` are faked (no network)."""

    def gh(self, table=None, push_rc=0, push_err="", wt_rc=0):
        self.calls: list = []
        real = pr_ops._run

        def run(args, cwd=None, timeout=120):
            line = " ".join(args)
            is_wt = any(a.endswith("wt.py") for a in args)
            if args[0] != "gh" and "push" not in args and not is_wt:
                return real(args, cwd, timeout)
            self.calls.append(line)
            for k, v in (table or {}).items():
                if line.startswith(k):
                    return v() if callable(v) else v  # a callable answers from the state of the fake
            if is_wt:
                return R(wt_rc, "", "wt failed" if wt_rc else "")
            return R(push_rc, "", push_err) if "push" in args else R(0)

        p = mock.patch.object(pr_ops, "_run", side_effect=run)
        p.start()
        self.addCleanup(p.stop)

    def release(self, pr):
        return pr_ops._release(self.d, self.d, self.cfg, "s", "stage/s", pr)

    def land(self):
        self.git("checkout", "-q", "master")
        self.git("merge", "-q", "--ff-only", "stage/s")  # fast-forward: the stage work is on master

    def merged(self, sha):
        return {
            "gh pr view stage/s --json state": R(0, json.dumps({"state": "MERGED"})),
            "gh pr view stage/s --json mergeCommit": R(0, json.dumps({"mergeCommit": {"oid": sha}})),
        }

    def test_local_tag_on_release_head_when_landed_without_pr(self):
        self.land()
        self.gh()
        note = self.release(False)
        self.assertEqual(note, f"tagged v0.1.5 on {self.rev('master')[:8]} (local-only repo, no release)")
        self.assertEqual(self.rev("v0.1.5^{commit}"), self.rev("master"))
        self.assertFalse([c for c in self.calls if "push" in c])

    def test_untagged_release_head_proceeds_even_when_nothing_landed(self):
        self.gh()
        self.assertIn("tagged v0.1.5", self.release(False))  # release head = base, not yet tagged

    def test_adds_nothing_and_already_tagged_returns_early(self):
        self.land()
        self.git("tag", "-a", "v0.1.5", "-m", "v0.1.5")
        self.gh()
        self.assertEqual(self.release(False), "stage/s adds nothing to master and v0.1.5 is already tagged: no release")
        self.assertEqual(self.calls, [])

    def test_merged_pr_pushes_tag_and_creates_release_with_changelog_notes(self):
        sha = self.rev("stage/s")
        self.w("CHANGELOG.md", RELEASE_NOTES)
        self.gh({**self.merged(sha), "gh release view": R(1)})
        note = self.release(True)
        self.assertEqual(note, f"released v0.1.5 on {sha[:8]}")
        self.assertEqual(self.rev("v0.1.5^{commit}"), sha)
        self.assertIn("git -c credential.helper=!gh auth git-credential push origin v0.1.5", self.calls)
        create = [c for c in self.calls if c.startswith("gh release create")][0]
        self.assertEqual(create, "gh release create v0.1.5 --verify-tag --title v0.1.5 --notes - fixed the thing")

    def test_merge_commit_missing_locally_is_fetched_first(self):
        self.gh(self.merged("0" * 40))
        with self.assertRaises(pr_ops.Blocked) as cm:
            self.release(True)
        self.assertIn("fetch of origin/master failed", str(cm.exception))

    def test_merged_pr_without_changelog_uses_generated_notes(self):
        sha = self.rev("stage/s")
        self.gh({**self.merged(sha), "gh release view": R(1)})
        self.release(True)
        create = [c for c in self.calls if c.startswith("gh release create")][0]
        self.assertTrue(create.endswith("--generate-notes"), create)

    def test_open_pr_blocks_release(self):
        self.gh({"gh pr view stage/s --json state": R(0, json.dumps({"state": "OPEN"}))})
        with self.assertRaisesRegex(pr_ops.Blocked, "not MERGED: finish master-merged first"):
            self.release(True)

    def test_tag_on_another_commit_blocks(self):
        self.git("tag", "-a", "v0.1.5", "stage/s", "-m", "old")  # stage head is not the release head
        self.gh()
        with self.assertRaisesRegex(pr_ops.Blocked, "tag v0.1.5 already exists on another commit"):
            self.release(False)

    def test_push_failure_blocks(self):
        sha = self.rev("stage/s")
        self.w("CHANGELOG.md", RELEASE_NOTES)
        self.gh({**self.merged(sha), "gh release view": R(1)}, push_rc=1, push_err="rejected")
        with self.assertRaisesRegex(pr_ops.Blocked, "push of v0.1.5 failed: rejected"):
            self.release(True)
        self.assertFalse([c for c in self.calls if c.startswith("gh release create")])

    def test_existing_release_is_not_created_again(self):
        sha = self.rev("stage/s")
        self.gh({**self.merged(sha), "gh release view": R(0)})
        self.assertEqual(self.release(True), f"tagged v0.1.5 on {sha[:8]}; release already exists")
        self.assertFalse([c for c in self.calls if c.startswith("gh release create")])

    def test_release_create_failure_blocks(self):
        sha = self.rev("stage/s")
        self.gh({**self.merged(sha), "gh release view": R(1), "gh release create": R(1, "", "boom")})
        with self.assertRaisesRegex(pr_ops.Blocked, "gh release create failed: boom"):
            self.release(True)

    def test_non_semver_version_blocks(self):
        self.git("checkout", "-q", "master")
        self.w("VERSION", "weird\n")
        self.git("commit", "-qam", "weird")
        self.gh()
        with self.assertRaisesRegex(pr_ops.Blocked, "not X.Y.Z"):
            self.release(False)


class Flags(unittest.TestCase):
    def test_release_default_on_off_flag(self):
        d = tracked_mkdtemp()
        p = Path(d, "TASKS.md")
        p.write_text("flags: docs\n", encoding="utf-8")
        self.assertTrue(tasks.parse_flags(str(p))["release"])
        p.write_text("flags: no-release\n", encoding="utf-8")
        self.assertFalse(tasks.parse_flags(str(p))["release"])


class CleanupReal(ReleaseReal):
    """_cleanup on a real repo with worktrees on: `wt.py clean` is faked, git is real."""

    def cleanup(self):
        a = SimpleNamespace(dir=self.d, stage="s", name="s")
        return pr_ops._cleanup(a, self.d, {**self.cfg, "worktrees": True}, "stage/s")

    def test_unlanded_stage_blocks_before_wt_clean_deletes_job_branches(self):
        self.gh({"gh pr view stage/s --json state": R(0, json.dumps({"state": "OPEN"}))})
        with self.assertRaisesRegex(pr_ops.Blocked, "has work that is not in master"):
            self.cleanup()
        self.assertFalse([c for c in self.calls if "wt.py" in c])
        self.assertEqual(subprocess.run(["git", "-C", self.d, "rev-parse", "--verify", "-q", "refs/heads/stage/s"], check=False).returncode, 0)

    def test_failed_wt_clean_blocks(self):
        self.land()
        self.gh({"gh pr view stage/s --json state": R(0, json.dumps({"state": "MERGED"}))}, wt_rc=1)
        with self.assertRaisesRegex(pr_ops.Blocked, "wt.py clean failed"):
            self.cleanup()

    def test_landed_stage_is_cleaned(self):
        self.land()
        self.gh({"gh pr view stage/s --json state": R(0, json.dumps({"state": "MERGED"}))})
        note = self.cleanup()
        self.assertIn("deleted stage/s", note)
        self.assertEqual(subprocess.run(["git", "-C", self.d, "rev-parse", "--verify", "-q", "refs/heads/stage/s"], check=False).returncode, 1)


class MergeRerun(ReleaseReal):
    """`merge` re-run after `gh pr merge --delete-branch` removed the stage branch (local and origin)."""

    def test_rerun_after_merge_reports_already_merged_without_push(self):
        self.git("checkout", "-q", "master")
        self.git("branch", "-q", "-D", "stage/s")
        self.gh({"gh pr view stage/s --json state": R(0, json.dumps({"state": "MERGED"}))}, push_rc=1, push_err="src refspec stage/s does not match any")
        verdict, text = pr_ops.run("merge", self.d, "s", "s")
        self.assertEqual((verdict, text), ("DONE", "PR already merged"))
        self.assertFalse([c for c in self.calls if " push " in f" {c} "])


class MergeLocalCleanup(ReleaseReal):
    """`gh pr merge --delete-branch` merged the PR but could not delete the local branch."""

    def test_merged_remotely_is_not_blocked_by_local_cleanup_failure(self):
        merged = {"done": False}

        def state():
            return R(0, json.dumps({"state": "MERGED" if merged["done"] else "OPEN"}))

        def merge():
            merged["done"] = True  # the PR merges, then the local branch delete fails
            return R(1, "", "failed to delete local branch stage/s: checked out")

        self.gh(
            {
                "gh pr view stage/s --json state": state,
                "gh pr view stage/s --json isDraft": R(0, json.dumps({"isDraft": False})),
                "gh pr view stage/s --json mergeable": R(0, json.dumps({"mergeable": "MERGEABLE"})),
                "gh pr merge": merge,
            }
        )
        verdict, text = pr_ops.run("merge", self.d, "s", "s")
        self.assertEqual(verdict, "DONE", text)
        self.assertIn("squash-merged", text)


class LandedReal(RepoCase):
    """_landed(pr=False) on real history: squash, rebase, and a release branch that moved on."""

    def commit(self, name, text, msg):
        self.w(name, text)
        self.git("add", "-A")
        self.git("commit", "-qm", msg)

    def landed(self):
        return pr_ops._landed(self.d, "master", "stage/s", pr=False)

    def test_squash_merged_stage_then_release_moves_on(self):
        self.git("checkout", "-q", "master")
        self.commit("FEATURE", "work\n", "feat: squash of stage")
        self.commit("VERSION", "0.1.6\n", "chore: unrelated")
        self.assertEqual(self.landed(), "")

    def test_squash_of_several_commits_then_release_moves_on(self):
        self.git("checkout", "-q", "stage/s")
        self.commit("FEATURE", "work\nmore\n", "feat: more")
        self.git("checkout", "-q", "master")
        self.commit("FEATURE", "work\nmore\n", "feat: squash of stage")
        self.commit("VERSION", "0.1.6\n", "chore: unrelated")
        self.assertEqual(self.landed(), "")

    def test_rebased_stage_then_release_moves_on(self):
        self.git("checkout", "-q", "master")
        self.commit("VERSION", "0.1.6\n", "chore: unrelated")
        self.git("checkout", "-q", "stage/s")
        self.git("rebase", "-q", "master")
        self.git("checkout", "-q", "master")
        self.git("merge", "-q", "--ff-only", "stage/s")
        self.commit("OTHER", "x\n", "chore: another")
        self.assertEqual(self.landed(), "")

    def test_squash_plus_new_unmerged_stage_commit_is_not_landed(self):
        self.git("checkout", "-q", "master")
        self.commit("FEATURE", "work\n", "feat: squash of stage")
        self.commit("VERSION", "0.1.6\n", "chore: unrelated")
        self.git("checkout", "-q", "stage/s")
        self.commit("NEW", "not merged\n", "feat: new work after the squash")
        self.assertNotEqual(self.landed(), "")

    def test_stage_not_on_release_is_not_landed(self):
        self.git("checkout", "-q", "master")
        self.commit("VERSION", "0.1.6\n", "chore: unrelated")
        self.assertNotEqual(self.landed(), "")


class LocalMergeRepo(RepoCase):
    """_local_merge_repo: squash the stage into master in a local-only repo."""

    def local(self):
        return pr_ops._local_merge_repo(self.d, self.cfg, "stage/s", "s", self.d)

    def head(self):
        return subprocess.run(["git", "-C", self.d, "symbolic-ref", "--short", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    def test_squashes_and_stays_on_the_release_branch(self):
        self.assertEqual(self.head(), "stage/s")
        self.assertIn("squashed stage/s into master", self.local())
        self.assertEqual(self.head(), "master")  # not back on the stage branch: it is released
        self.assertEqual(self.r("FEATURE"), "work\n")
        self.assertEqual(subprocess.run(["git", "-C", self.d, "show", "master:FEATURE"], check=True, capture_output=True, text=True).stdout, "work\n")

    def test_untracked_file_blocks_before_switching(self):
        self.w("junk", "not tracked\n")
        with self.assertRaisesRegex(pr_ops.Blocked, "working tree is dirty"):
            self.local()
        self.assertEqual(self.head(), "stage/s")
        self.assertEqual(self.r("junk"), "not tracked\n")


class StaleRelease(RepoCase):
    """version-bump and _open_pr on a repo with a real local bare origin (no network)."""

    def add_origin(self):
        self.origin = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q", "--bare", self.origin], check=True)
        self.git("remote", "add", "origin", self.origin)
        self.git("push", "-q", "origin", "master")

    def upstream_commit(self):
        """Another clone pushes to origin/master: our origin/master ref is now stale."""
        other = tracked_mkdtemp()
        subprocess.run(["git", "clone", "-q", self.origin, other], check=True, capture_output=True)
        subprocess.run(["git", "-C", other, "commit", "-q", "--allow-empty", "-m", "chore: upstream"], check=True)
        subprocess.run(["git", "-C", other, "push", "-q", "origin", "HEAD:master"], check=True, capture_output=True)
        return subprocess.run(["git", "-C", self.origin, "rev-parse", "master"], check=True, capture_output=True, text=True).stdout.strip()

    def test_version_bump_fetches_the_release_head_first(self):
        self.add_origin()
        new = self.upstream_commit()
        self.assertNotEqual(self.rev("origin/master"), new)  # stale before the bump
        self.assertIn("patch bump", pr_ops._version_bump(self.d, self.d, self.cfg, "s", "stage/s"))
        self.assertEqual(self.rev("origin/master"), new)

    def test_open_pr_counts_against_origin_when_the_local_release_branch_is_missing(self):
        self.add_origin()
        self.git("branch", "-q", "-D", "master")  # no local release branch (we are on stage/s)
        self.git("reset", "-q", "--hard", "origin/master")  # stage adds nothing to origin/master
        calls = []

        real = pr_ops._run

        def run(args, cwd=None, timeout=120):
            if args[0] != "gh":
                return real(args, cwd, timeout)
            calls.append(" ".join(args))
            return R(0, "[]") if args[:3] == ["gh", "pr", "list"] else R(0, "https://x/1")

        with mock.patch.object(pr_ops, "_run", side_effect=run):
            note = pr_ops._open_pr(self.d, self.d, self.cfg, "s", "stage/s")
        self.assertIn("no commit beyond the release branch", note)
        self.assertFalse([c for c in calls if c.startswith("gh pr create")])


class CleanRepoNoLocalRelease(StaleRelease):
    """_clean_repo when the local release branch is missing and only origin/<release> exists."""

    def test_deletes_the_landed_stage_branch_without_a_local_release_branch(self):
        self.add_origin()
        self.git("branch", "-q", "-D", "master")  # on stage/s; origin/master already has its work
        self.git("update-ref", "refs/remotes/origin/master", "refs/heads/stage/s")
        note = pr_ops._clean_repo(SimpleNamespace(dir=self.d, stage="s"), self.d, self.cfg, "stage/s", False)
        self.assertIn("deleted stage/s", note)
        self.assertEqual(self.head(), "master")

    def head(self):
        return subprocess.run(["git", "-C", self.d, "symbolic-ref", "--short", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


class SweepMergeFailure(ReleaseReal):
    """_sweep: a green Dependabot PR whose squash merge fails is a failure, not a pending PR."""

    def test_failed_merge_is_counted_as_failed_not_pending(self):
        green = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]
        self.gh(
            {
                "gh pr list": R(0, json.dumps([{"number": 7, "title": "bump x", "statusCheckRollup": green}])),
                "gh pr merge 7": R(1, "", "Pull request is not mergeable"),
            }
        )
        text = pr_ops._sweep(self.d, self.cfg)
        self.assertIn("#7 skipped (merge failed:", text)
        self.assertIn("1 merge failed", text)
        self.assertIn("0 skipped pending", text)


class VersionBumpNoOrigin(RepoCase):
    """version-bump in a local-only repo: no origin remote and no origin/<release> ref."""

    def test_no_origin_and_no_remote_ref_falls_back_to_local_release_branch(self):
        self.git("update-ref", "-d", "refs/remotes/origin/master")  # setUp made it; a local-only repo has none
        self.assertIn("patch bump v0.1.5 -> v0.1.6", self.bump())
        self.assertEqual(self.r("VERSION"), "0.1.6\n")

    def test_unreachable_origin_blocks_with_fetch_error(self):
        self.git("remote", "add", "origin", "/nonexistent/origin.git")
        with self.assertRaisesRegex(pr_ops.Blocked, r"fetch of origin/master failed: fatal: .*nonexistent"):
            self.bump()  # git's own stderr tail is quoted


if __name__ == "__main__":
    unittest.main()
