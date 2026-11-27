"""orch retry whose rebase conflicts: the job worktree ends with no rebase state, and the BLOCKED reason lists the job's own commits (doc-only ones marked) with the rebuild command and the retry line. The code drops no commit."""

import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import cli_orch, worktree  # noqa: E402  # sys.path set on the line above, before the pipeline import
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402  # sys.path set on the line above, before the pipeline import


def sh(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class RetryPickConflictTest(unittest.TestCase):
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
            json.dump({"repo": "main", "branch_work": "dev"}, f)
        sh(self.m, "init", "-q", "-b", "dev")
        self.put(self.m, "f", "x")
        with open(f"{self.t}/.work/s/pick-step.md", "w") as f:
            f.write("Predicted paths: src/x")
        self.info = worktree.create(self.t, "s", "pick-step")
        self.addCleanup(worktree.remove, self.t, "s", "pick-step", True)

    def put(self, repo, name, text, msg=None):
        os.makedirs(os.path.dirname(os.path.join(repo, name)) or repo, exist_ok=True)
        with open(os.path.join(repo, name), "w") as f:
            f.write(text)
        sh(repo, "add", name)
        sh(repo, "commit", "-qm", msg or name)

    def make_conflict(self):
        root = self.info["root"]
        self.put(root, "docs/notes.md", "doc change", msg="docs: notes")
        self.put(root, "f", "mine", msg="feat: mine")
        self.put(self.m, "f", "theirs", msg="main: theirs")

    def retry(self):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="pick-step", note="", from_step="", addendum="")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            cli_orch.cmd_reset(a)
        return str(cm.exception)

    def log(self):
        with open(f"{self.t}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_no_rebase_state_left(self):
        self.make_conflict()
        self.retry()
        root = self.info["root"]
        git_dir = sh(root, "rev-parse", "--path-format=absolute", "--git-dir")
        self.assertFalse(os.path.exists(os.path.join(git_dir, "rebase-merge")))
        self.assertFalse(os.path.exists(os.path.join(git_dir, "rebase-apply")))
        self.assertEqual(sh(root, "status", "--porcelain"), "")

    def test_reason_lists_own_commits_and_pick(self):
        self.make_conflict()
        root = self.info["root"]
        docs_sha = sh(root, "rev-parse", "HEAD~1")
        code_sha = sh(root, "rev-parse", "HEAD")
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        rep = [e for e in self.log() if e["ev"] == "report"]
        self.assertEqual(len(rep), 1)
        reason = rep[0]["reason"]
        self.assertIn(f"  {docs_sha} docs: notes [doc-only]", reason)
        self.assertIn(f"  {code_sha} feat: mine", reason)
        self.assertNotIn(f"{code_sha} feat: mine [doc-only]", reason)
        reset_line = f"rebuild: git -C {root} reset --hard {self.info['base']}"
        pick_line = f"rebuild: git -C {root} cherry-pick {docs_sha} {code_sha}"
        retry_line = "then retry: orch.py retry s --step pick-step"
        lines = reason.split("\n")
        self.assertIn(reset_line, lines)
        self.assertIn(pick_line, lines)
        self.assertIn(retry_line, lines)
        self.assertLess(lines.index(reset_line), lines.index(pick_line))  # move onto base first, then re-apply own commits
        self.assertLess(lines.index(pick_line), lines.index(retry_line))
        self.assertNotIn("--pick", reason)
        self.assertFalse([e for e in self.log() if e["ev"] == "retry"])
        self.assertEqual(sh(root, "rev-parse", "HEAD"), code_sha)

    def test_printed_retry_line_parses(self):
        self.make_conflict()
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        line = next(x for x in reason.split("\n") if x.startswith("then retry: "))
        argv = shlex.split(line.removeprefix("then retry: "))
        self.assertEqual(argv[:2], ["orch.py", "retry"])
        with unittest.mock.patch.object(cli_orch, "cmd_reset") as reset:
            cli_orch.main(argv[1:])  # the real retry parser: an argparse error would raise SystemExit here
        a = reset.call_args.args[0]
        self.assertEqual((a.name, a.step), ("s", "pick-step"))

    def patch_rebase(self, fake):
        """Route every git call through `fake(real_run, path, args, kw)`; the real run stays available to it."""
        real = worktree.gitx.run
        p = unittest.mock.patch.object(worktree.gitx, "run", side_effect=lambda path, *args, **kw: fake(real, path, args, kw))
        p.start()
        self.addCleanup(p.stop)

    def assert_no_rebase_state(self):
        git_dir = sh(self.info["root"], "rev-parse", "--path-format=absolute", "--git-dir")
        self.assertFalse(os.path.exists(os.path.join(git_dir, "rebase-merge")))
        self.assertFalse(os.path.exists(os.path.join(git_dir, "rebase-apply")))

    def test_timeout_leaves_no_rebase_state(self):
        def hang_after_rebase(real, path, args, kw):
            if args[:1] == ("rebase",) and args[1] not in ("--abort", "--quit"):
                real(path, *args, **kw)  # the rebase really runs and conflicts, then git 'hangs'
                raise subprocess.TimeoutExpired(args, kw.get("timeout"))
            return real(path, *args, **kw)

        self.make_conflict()
        self.patch_rebase(hang_after_rebase)
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        self.assert_no_rebase_state()
        self.assertEqual(sh(self.info["root"], "status", "--porcelain"), "")
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        self.assertIn("own commits not on", reason)  # the abort worked, so the commits are listed
        self.assertIn("rebuild: git -C", reason)

    def test_git_error_leaves_no_rebase_state(self):
        def fail_after_rebase(real, path, args, kw):
            if args[:1] == ("rebase",) and args[1] not in ("--abort", "--quit"):
                real(path, *args, **kw)  # the rebase really conflicts, then git exits non-zero
                return subprocess.CompletedProcess(args, 1, "", "fatal: boom")
            return real(path, *args, **kw)

        self.make_conflict()
        self.patch_rebase(fail_after_rebase)
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        self.assert_no_rebase_state()
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        self.assertIn("rebuild: git -C", reason)

    def test_stuck_rebase_is_reported_not_listed(self):
        def abort_does_nothing(real, path, args, kw):
            if args[:1] == ("rebase",) and args[1] in ("--abort", "--quit"):
                return subprocess.CompletedProcess(args, 0, "", "")  # both aborts fail to clear the state
            return real(path, *args, **kw)

        self.make_conflict()
        root = self.info["root"]
        self.addCleanup(
            subprocess.run, ["git", "-C", root, "rebase", "--abort"], capture_output=True, check=False
        )  # the state is real: clear it after the test
        self.patch_rebase(abort_does_nothing)
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        self.assertIn(f"rebase still in progress in {root} (abort failed)", reason)
        self.assertNotIn("rebuild:", reason)  # a partial pick is not a base to rebuild from
        self.assertNotIn("own commits not on", reason)
        git_dir = sh(root, "rev-parse", "--path-format=absolute", "--git-dir")
        self.assertTrue(os.path.exists(os.path.join(git_dir, "rebase-merge")))  # left as found: the lead aborts it by hand

    def test_quit_after_failed_abort_is_not_listed(self):
        def abort_fails(real, path, args, kw):
            if args[:2] == ("rebase", "--abort"):
                return subprocess.CompletedProcess(args, 1, "", "fatal: boom")  # --quit then clears the state but leaves HEAD detached
            return real(path, *args, **kw)

        self.make_conflict()
        root = self.info["root"]
        self.addCleanup(subprocess.run, ["git", "-C", root, "checkout", "-q", self.info["branch"]], capture_output=True, check=False)
        self.patch_rebase(abort_fails)
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        self.assertIn(f"rebase still in progress in {root} (abort failed)", reason)
        self.assertNotIn("rebuild:", reason)  # the detached HEAD would list a partial pick, not the job's commits

    def test_state_check_timeout_is_reported(self):
        def state_hangs(real, path, args, kw):
            if "--git-path" in args:
                raise subprocess.TimeoutExpired(args, kw.get("timeout"))
            return real(path, *args, **kw)

        self.make_conflict()
        self.patch_rebase(state_hangs)
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        self.assertIn("(abort failed)", reason)

    def test_commit_list_timeout_says_unknown(self):
        def list_hangs(real, path, args, kw):
            if args[:1] == ("rev-list",) and "--reverse" in args:
                raise subprocess.TimeoutExpired(args, kw.get("timeout"))
            return real(path, *args, **kw)

        self.make_conflict()
        self.patch_rebase(list_hangs)
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        reason = [e for e in self.log() if e["ev"] == "report"][-1]["reason"]
        self.assertIn("unknown (git timed out)", reason)
        self.assertNotIn(": none", reason)


if __name__ == "__main__":
    unittest.main()
