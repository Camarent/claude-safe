"""PR steps (pr-open, pr-push, pr-ready), release message and branch cleanup: fake `gh` on PATH, bare scratch origin, no network."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
os.environ.setdefault("ORCH_NO_INDEX", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, graph, phases, pr_mode, pr_ops
from tests.support.gitrepo import git

_INDEX_PATCH = mock.patch.object(graph, "run_index", return_value=0)


def setUpModule():
    _INDEX_PATCH.start()


def tearDownModule():
    _INDEX_PATCH.stop()


FAKE_GH = """#!/bin/sh
echo "$@" >> "$GH_LOG"
case "$1 $2" in
  "auth status") exit 0 ;;
  "pr list") if [ -f "$GH_LOG.pr" ]; then echo '[{"number": 7, "url": "u"}]'; else echo '[]'; fi ;;
  "pr create") touch "$GH_LOG.pr"; echo "https://example/pull/7" ;;
  "pr ready") [ -f "$GH_LOG.readyfail" ] && { echo "cannot" >&2; exit 1; }; exit 0 ;;
  "pr view")
    case "$*" in
      *headRefOid*) echo '{"headRefOid": "sha-ok"}' ;;
      *isDraft*) cat "$GH_LOG.draft" 2>/dev/null || echo '{"isDraft": false}' ;;
      *mergeable*) cat "$GH_LOG.mergeable" 2>/dev/null || echo '{"mergeable": "MERGEABLE"}' ;;
      *) cat "$GH_LOG.state" 2>/dev/null || exit 1 ;;
    esac ;;
  "pr merge") [ -f "$GH_LOG.mergefail" ] && { echo "denied" >&2; exit 1; }; exit 0 ;;
  "pr checks") echo "pr checks must not be called" >&2; exit 1 ;;
  "run list") cat "$GH_LOG.runs" 2>/dev/null || echo '[{"name": "ci", "status": "completed", "conclusion": "success"}]' ;;
esac
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.d = os.path.join(self.root, "w")
        self.origin = os.path.join(self.root, "origin.git")
        os.makedirs(self.d)
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")
        bin_ = os.path.join(self.root, "bin")
        os.makedirs(bin_)
        gh = os.path.join(bin_, "gh")
        Path(gh).write_text(FAKE_GH, encoding="utf-8")
        os.chmod(gh, 0o755)
        self.log = os.path.join(self.root, "gh.log")
        self.old = dict(os.environ)
        os.environ["PATH"] = bin_ + os.pathsep + os.environ["PATH"]
        os.environ["GH_LOG"] = self.log
        Path(self.d, "pipeline.json").write_text('{"qualityrun": false, "testrun": false}', encoding="utf-8")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def add_origin(self):
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", self.origin], check=True)
        git(self.d, "remote", "add", "origin", self.origin)
        git(self.d, "push", "-q", "origin", "master")

    def commit_on_stage(self):
        git(self.d, "switch", "-q", "-c", "stage/x")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "work")

    def calls(self):
        return Path(self.log).read_text().splitlines() if os.path.exists(self.log) else []


class RunTest(Base):
    def test_open_push_ready_order_and_draft(self):
        self.add_origin()
        self.commit_on_stage()
        self.assertEqual(pr_ops.run("open", self.d, "s", "x")[0], "DONE")
        self.assertEqual(pr_ops.run("push", self.d, "s", "x")[0], "DONE")
        v, text = pr_ops.run("ready", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        c = self.calls()
        creates = [x for x in c if x.startswith("pr create")]
        self.assertEqual(len(creates), 1)  # the second run reused the open PR
        self.assertIn("--draft --base master", creates[0])
        order = [x for x in c if x.startswith(("pr create", "pr ready", "run list"))]
        self.assertTrue(order[0].startswith("pr create") and order[1].startswith("pr ready") and order[2].startswith("run list"))
        self.assertFalse([x for x in c if x.startswith("pr checks")])
        self.assertIn("stage/x", git(self.d, "ls-remote", "--heads", "origin"))

    def test_empty_stage_branch_is_not_pushed_and_gets_no_pr(self):
        self.add_origin()
        git(self.d, "switch", "-q", "-c", "stage/x")
        for what in ("open", "push", "ready", "merge"):
            v, text = pr_ops.run(what, self.d, "s", "x")
            self.assertEqual(v, "DONE", text)
            self.assertIn("no commit beyond the release branch", text)
        self.assertFalse([x for x in self.calls() if x.startswith("pr create")])
        self.assertNotIn("stage/x", git(self.d, "ls-remote", "--heads", "origin"))
        v, text = pr_ops.run("cleanup", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        self.assertNotIn("stage/x", git(self.d, "branch", "--list"))

    def test_github_origin_check_and_slug(self):
        from pipeline import pr_mode

        git(self.d, "remote", "add", "origin", "git@gitlab.com:o/r.git")
        a = argparse.Namespace(dir=self.d, stage="s")
        self.assertFalse(pr_mode.github_origin(a))
        self.assertIn("not on a GitHub host", pr_mode.down_reason(a))
        git(self.d, "remote", "set-url", "origin", "https://github.com/o/r.git")
        self.assertTrue(pr_mode.github_origin(a))
        self.assertEqual(pr_mode.origin_parts(self.d), ("github.com", "o/r"))

    def test_red_checks_are_blocked_with_names(self):
        self.add_origin()
        self.commit_on_stage()
        Path(self.log + ".runs").write_text(
            '[{"name": "unit", "status": "completed", "conclusion": "failure"}, {"name": "lint", "status": "completed", "conclusion": "success"}]'
        )
        v, text = pr_ops.run("ready", self.d, "s", "x")
        self.assertEqual(v, "BLOCKED")
        self.assertIn("unit", text)
        self.assertNotIn("lint", text)


class MergeTest(Base):
    def setUp(self):
        super().setUp()
        Path(self.log + ".state").write_text('{"state": "OPEN"}')
        Path(f"{self.d}/.work/s/reports").mkdir(parents=True, exist_ok=True)
        Path(f"{self.d}/.work/s/TASKS.md").write_text("# Stage: s\nGoal: Add the demo link.\npr-title: docs(readme): add demo link\n", encoding="utf-8")

    def merge(self):
        self.add_origin()
        self.commit_on_stage()
        return pr_ops.run("merge", self.d, "s", "x")

    def test_squash_merges_a_green_ready_pr_with_the_title_line(self):
        v, text = self.merge()
        self.assertEqual(v, "DONE", text)
        m = [x for x in self.calls() if x.startswith("pr merge")]
        self.assertEqual(len(m), 1)
        self.assertIn("--squash --delete-branch --subject docs(readme): add demo link", m[0])

    def test_already_merged_does_nothing(self):
        Path(self.log + ".state").write_text('{"state": "MERGED"}')
        v, text = self.merge()
        self.assertEqual((v, text), ("DONE", "PR already merged"))
        self.assertFalse([x for x in self.calls() if x.startswith("pr merge")])

    def test_draft_conflict_red_and_failed_merge_are_blocked(self):
        Path(self.log + ".draft").write_text('{"isDraft": true}')
        self.assertIn("draft", self.merge()[1])
        Path(self.log + ".draft").write_text('{"isDraft": false}')
        Path(self.log + ".mergeable").write_text('{"mergeable": "CONFLICTING"}')
        self.assertIn("CONFLICTING", pr_ops.run("merge", self.d, "s", "x")[1])
        Path(self.log + ".mergeable").write_text('{"mergeable": "MERGEABLE"}')
        Path(self.log + ".runs").write_text('[{"name": "unit", "status": "completed", "conclusion": "failure"}]')
        self.assertIn("unit", pr_ops.run("merge", self.d, "s", "x")[1])
        Path(self.log + ".runs").write_text('[{"name": "ci", "status": "completed", "conclusion": "success"}]')
        Path(self.log + ".mergefail").write_text("")
        v, text = pr_ops.run("merge", self.d, "s", "x")
        self.assertEqual(v, "BLOCKED")
        self.assertIn("gh pr merge failed", text)
        self.assertFalse([x for x in self.calls() if x.startswith("pr merge") and "--subject" not in x])

    def test_title_falls_back_to_a_conventional_chore_title(self):
        Path(f"{self.d}/.work/s/TASKS.md").write_text("# Stage: s\nGoal: Add the demo link.\npr-title: not conventional\n", encoding="utf-8")
        self.assertEqual(pr_ops._merge_title(self.d, "s", "stage/x"), "chore(s): add the demo link")

    def test_phase_stage_fallback_title_names_the_feature(self):
        stage = "feat/phases/01-first"
        Path(f"{self.d}/.work/{stage}").mkdir(parents=True)
        Path(f"{self.d}/.work/{stage}/TASKS.md").write_text("# Stage: x\nGoal: Add the demo link.\n", encoding="utf-8")
        self.assertEqual(pr_ops._merge_title(self.d, stage, "stage/x"), "chore(feat): add the demo link")


class ReadyFailTest(Base):
    def test_ready_failure_on_draft_is_blocked(self):
        self.add_origin()
        self.commit_on_stage()
        Path(self.log + ".readyfail").write_text("")
        Path(self.log + ".draft").write_text('{"isDraft": true}')
        v, text = pr_ops.run("ready", self.d, "s", "x")
        self.assertEqual(v, "BLOCKED")
        self.assertIn("draft", text)
        self.assertFalse([x for x in self.calls() if "--watch" in x])


class CleanupTest(Base):
    def test_unmerged_work_blocks_and_keeps_branch(self):
        self.commit_on_stage()
        Path(self.d, "g.txt").write_text("unmerged")
        git(self.d, "add", "g.txt")
        git(self.d, "commit", "-q", "-m", "more")
        v, text = pr_ops.run("cleanup", self.d, "s", "x")
        self.assertEqual(v, "BLOCKED")
        self.assertIn("not in master", text)
        self.assertIn("stage/x", git(self.d, "branch", "--list", "stage/x"))

    def test_after_real_squash_branch_is_deleted(self):
        git(self.d, "switch", "-q", "-c", "stage/x")
        Path(self.d, "f.txt").write_text("a")
        git(self.d, "add", "f.txt")
        git(self.d, "commit", "-q", "-m", "work")
        git(self.d, "switch", "-q", "master")
        git(self.d, "merge", "--squash", "stage/x")
        git(self.d, "commit", "-q", "-m", "squash")
        self.assertEqual(pr_ops.run("cleanup", self.d, "s", "x")[0], "DONE")
        self.assertEqual(git(self.d, "branch", "--list", "stage/x"), "")

    def test_merged_pr_allows_delete(self):
        self.commit_on_stage()
        git(self.d, "commit", "-q", "--allow-empty", "-m", "more")
        Path(self.log + ".state").write_text('{"state": "MERGED"}')
        self.assertEqual(pr_ops.run("cleanup", self.d, "s", "x")[0], "DONE")
        self.assertEqual(git(self.d, "branch", "--list", "stage/x"), "")

    def test_delete_failure_is_blocked(self):
        self.commit_on_stage()
        wt = os.path.join(self.root, "wt")
        git(self.d, "switch", "-q", "master")
        git(self.d, "worktree", "add", "-q", wt, "stage/x")  # a branch checked out elsewhere cannot be deleted
        v, text = pr_ops.run("cleanup", self.d, "s", "x")
        self.assertEqual(v, "BLOCKED")
        self.assertIn("could not delete", text)

    def test_local_repo_cleanup_is_idempotent(self):
        self.commit_on_stage()
        self.assertEqual(pr_ops.run("cleanup", self.d, "s", "x")[0], "DONE")
        self.assertEqual(git(self.d, "symbolic-ref", "--short", "HEAD"), "master")
        self.assertEqual(git(self.d, "branch", "--list", "stage/x"), "")
        self.assertEqual(pr_ops.run("cleanup", self.d, "s", "x")[0], "DONE")  # branch already gone

    def test_pushed_repo_deletes_remote_branch_and_fast_forwards(self):
        self.add_origin()
        self.commit_on_stage()
        git(self.d, "push", "-q", "origin", "stage/x")
        other = os.path.join(self.root, "other")
        subprocess.run(["git", "clone", "-q", self.origin, other], check=True)
        git(other, "commit", "-q", "--allow-empty", "-m", "squash on github")
        git(other, "push", "-q", "origin", "master")
        v, text = pr_ops.run("cleanup", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        self.assertNotIn("stage/x", git(self.d, "ls-remote", "--heads", "origin"))
        self.assertEqual(git(self.d, "rev-parse", "HEAD"), git(other, "rev-parse", "HEAD"))
        self.assertEqual(pr_ops.run("cleanup", self.d, "s", "x")[0], "DONE")

    def test_fixed_branch_work_gets_no_cleanup_step(self):
        Path(self.d, "pipeline.json").write_text('{"branch_work": "dev"}', encoding="utf-8")
        a = argparse.Namespace(dir=self.d, stage="s", dry_run=False)
        self.assertFalse(pr_mode.cleanup_active(a))
        self.assertFalse(pr_mode.pr_active(a))


TASKS = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |

## Graph
wave 1: task-1
flags: no-test no-audit no-retro no-index
"""


class FlowTest(Base):
    def go(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_orch.main([args[0], "--stage", "s", "--dir", self.d, *args[1:]])
        return json.loads(out.getvalue())

    def setUp(self):
        super().setUp()
        os.makedirs(f"{self.d}/.work/s/reports")
        Path(f"{self.d}/.work/s/TASKS.md").write_text(TASKS, encoding="utf-8")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "files")

    def dispatched(self):
        lines = Path(f"{self.d}/.work/s/orch.jsonl").read_text().splitlines()
        return [e["task"] for e in map(json.loads, lines) if e["ev"] == "dispatch"]

    def sequence(self):
        self.before = self.dispatched() if os.path.exists(f"{self.d}/.work/s/orch.jsonl") else []
        for _ in range(30):
            r = self.go("next")
            if r["action"] != "dispatch":
                return self.dispatched()[len(self.before) :], r
            for d in r["dispatches"]:
                verdict = {"dev": "DONE", "reviewer": "PASS"}.get(d["agent"], "DONE")
                Path(f"{self.d}/.work/s/reports/report-{d['id']}.md").write_text(f"VERDICT: {verdict}\n", encoding="utf-8")
                self.go("report", "--id", d["id"])
        self.fail("no end")

    def test_pushed_repo_steps_in_order_then_release_message_and_cleanup(self):
        self.add_origin()
        seen, _r = self.sequence()
        self.assertEqual([x for x in seen if x.startswith("pr-")], ["pr-push-w1", "pr-ready"])
        self.assertGreater(seen.index("pr-push-w1"), seen.index("task-1"))
        # the squash merge is a script step now: no lead pause, cleanup follows it before push and republish
        self.assertLess(seen.index("pr-ready"), seen.index("master-merged"))
        self.assertLess(seen.index("master-merged"), seen.index("branch-cleanup"))

    def test_no_origin_has_no_pr_steps_and_local_squash_message(self):
        seen, r = self.sequence()
        self.assertFalse([x for x in seen if x.startswith("pr-")])
        self.assertIn("`local-merge` step already squashed `stage/s`", r["message"])
        self.assertFalse([x for x in self.calls() if x.startswith("pr ")])

    def test_pr_false_disables_pr_steps(self):
        self.add_origin()
        Path(self.d, "pipeline.json").write_text('{"pr": false, "qualityrun": false, "testrun": false}', encoding="utf-8")
        seen, r = self.sequence()
        self.assertFalse([x for x in seen if x.startswith("pr-")])
        self.assertIn("`local-merge` step already squashed", r["message"])
        self.assertIsNotNone(phases)


class NoChangeStageTest(unittest.TestCase):
    """A stage branch that adds nothing to the release branch gets no version bump, merge or release."""

    def test_empty_stage_branch_skips_bump_merge_and_release(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "master")
            Path(d, "f").write_text("x", encoding="utf-8")
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "base")
            git(d, "checkout", "-q", "-b", "stage/s")
            cfg = {"branch_release": "master", "release_script": "no-such-script.sh"}
            self.assertIn("no version bump", pr_ops._version_bump(d, d, cfg, "s", "stage/s"))
            with mock.patch.object(pr_ops, "_view", return_value=""):
                self.assertIn("nothing to merge", pr_ops._merge(d, d, cfg, "s", "stage/s"))

    def test_stage_branch_with_a_commit_still_reaches_the_release_script(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "master")
            Path(d, "f").write_text("x", encoding="utf-8")
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "base")
            git(d, "checkout", "-q", "-b", "stage/s")
            Path(d, "f").write_text("y", encoding="utf-8")
            git(d, "commit", "-q", "-am", "work")
            cfg = {"branch_release": "master", "release_script": "no-such-script.sh"}
            with self.assertRaises(pr_ops.Blocked):
                pr_ops._version_bump(d, d, cfg, "s", "stage/s")


if __name__ == "__main__":
    unittest.main()
