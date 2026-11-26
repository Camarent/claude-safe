"""ops_steps.py: cleanup, push and digest as script steps (real tmp repos and stage folders)."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ops_steps
from tests.support.gitrepo import git


class OpsStepsTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = os.path.realpath(t.name)
        self.root = os.path.join(self.d, ".work", "s")
        os.makedirs(os.path.join(self.root, "reports"))
        os.makedirs(os.path.join(self.root, "leads"))

    def put(self, rel, text="x\n"):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_cleanup_deletes_finished_files_and_keeps_the_rest(self):
        for rel in ("task-1.md", "task-1-list.md", "leads/l.md", "reports/report-a1.md"):
            self.put(rel)
        self.put("reports/report-a2.md", "VERDICT: BLOCKED\nwhy\n")
        self.put("reports/report-a3.md", "ESCALATE: hard\n")
        for rel in ("TASKS.md", "orch.jsonl", "notes.md"):
            self.put(rel)
        ok, lines = ops_steps.cleanup(self.d, "s")
        self.assertTrue(ok)
        self.assertEqual(sorted(os.listdir(self.root)), ["TASKS.md", "notes.md", "orch.jsonl", "reports"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "reports"))), ["report-a2.md", "report-a3.md"])
        self.assertEqual(lines[0], "deleted 4 files")

    def test_push_reports_local_only_and_pushed(self):
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "i")
        ok, lines = ops_steps.push(self.d)
        self.assertTrue(ok)
        self.assertIn("local-only", lines[0])
        bare = os.path.join(self.d, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        git(self.d, "remote", "add", "origin", bare)
        ok, lines = ops_steps.push(self.d)
        self.assertTrue(ok, lines)
        self.assertIn("pushed master", lines[0])
        self.assertEqual(subprocess.run(["git", "-C", bare, "rev-parse", "master"], check=False, capture_output=True, text=True).returncode, 0)

    def test_push_failure_is_fail(self):
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "i")
        git(self.d, "remote", "add", "origin", os.path.join(self.d, "missing.git"))
        ok, lines = ops_steps.push(self.d)
        self.assertFalse(ok)
        self.assertIn("error", lines[0])

    def test_push_with_only_an_upstream_remote_is_local_only(self):  # review2 tokens (3)
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "i")
        git(self.d, "remote", "add", "upstream", os.path.join(self.d, "missing.git"))
        ok, lines = ops_steps.push(self.d)
        self.assertTrue(ok, lines)
        self.assertIn("local-only", lines[0])

    def test_push_reaches_nested_repos_at_depth_two(self):  # review2 tokens (3)
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "i")
        sub = os.path.join(self.d, "libs", "x")
        os.makedirs(sub)
        git(sub, "init", "-q", "-b", "master")
        self.assertIn(sub, [r for r, _ in ops_steps.repos(self.d)])

    def test_bad_stage_is_fail_and_null_cost_is_zero(self):  # review2 tokens (3)
        for bad in ("", "..", "../x", "/abs", ".", "./", "a/./b", "a//."):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ops_steps.main(["cleanup", "--dir", self.d, "--stage", bad]), 1)
            self.assertTrue(out.getvalue().startswith("VERDICT: FAIL"), bad)
        with open(os.path.join(self.root, "metrics.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ev": "session", "agent": "dev", "cost_usd": None}) + "\n")
        self.assertTrue(ops_steps.digest(self.d, "s")[0])

    def test_digest_writes_the_file_and_main_prints_a_verdict_first(self):
        rows = [
            {"ev": "session", "agent": "dev", "model": "haiku", "task": "alpha", "verdict": "BLOCKED", "reason": "no glob", "cost_usd": 0.5},
            {"ev": "session", "agent": "reviewer", "model": "haiku", "task": "alpha", "verdict": "PASS", "cost_usd": 0.1},
            {"ev": "wave"},
        ]
        with open(os.path.join(self.root, "metrics.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ops_steps.main(["digest", "--dir", self.d, "--stage", "s"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().startswith("VERDICT: DONE\n"))
        self.assertIn("alpha BLOCKED: no glob", out.getvalue())
        self.assertTrue(os.path.exists(os.path.join(self.root, "retro", "retro-digest.md")))

    def test_ops_as_script_only_when_listed(self):
        from types import SimpleNamespace

        from pipeline.steps import ops_as_script, step

        a = SimpleNamespace(dir=self.d, stage="s")
        st = step("push-master", "ops", "haiku", "prompt", {"DONE"}, None, "why")
        self.assertIs(ops_as_script(a, {}, st, "push"), st)
        self.assertIs(ops_as_script(a, {"ops_scripts": ["digest"]}, st, "push"), st)
        got = ops_as_script(a, {"ops_scripts": ["push-master"]}, st, "push")
        self.assertEqual((got["agent"], got["model"], got["ok"]), ("script", None, {"DONE"}))
        self.assertIn(f"ops_steps.py push --dir {self.d} --stage s", got["prompt"])

    def test_cleanup_is_always_a_script_and_needs_no_env_md(self):
        from types import SimpleNamespace

        from pipeline.steps import ops_as_script, step

        a = SimpleNamespace(dir=self.d, stage="s")
        st = step("cleanup", "ops", "haiku", "prompt", {"DONE"}, None, "why")
        got = ops_as_script(a, {}, st, "cleanup")  # no ops_scripts setting at all
        self.assertEqual((got["agent"], got["model"]), ("script", None))
        self.assertFalse(os.path.exists(os.path.join(self.d, ".work", "ENV.md")))
        ok, _lines = ops_steps.cleanup(self.d, "s")
        self.assertTrue(ok)
        ok, lines = ops_steps.cleanup(self.d, "feat/phases/none")  # a feature folder that has no stage files yet
        self.assertTrue(ok, lines)

    def _repo_with_doc(self, text):
        git(self.d, "init", "-q", "-b", "master")
        with open(os.path.join(self.d, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        git(self.d, "add", "README.md")
        git(self.d, "commit", "-q", "-m", "i")

    def test_public_words_hit_on_public_repo_prints_file_line_and_fails(self):
        self._repo_with_doc("intro\nthe sauce is here\n")
        self.put_claude("This repo is public.\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}):
            ok, lines = ops_steps.public_words_gate(self.d)
            self.assertFalse(ok)
            self.assertEqual(lines, ["README.md:2: sauce", "add to docs/public-words-baseline.txt: README.md\tsauce\tthe sauce is here"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = ops_steps.main(["public-words", "--dir", self.d])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue().splitlines()[0], "VERDICT: FAIL")
        self.assertIn("README.md:2: sauce", out.getvalue())
        self.assertIn("add to docs/public-words-baseline.txt: README.md\tsauce\tthe sauce is here", out.getvalue())

    def _baseline(self, text):
        os.makedirs(os.path.join(self.d, "docs"), exist_ok=True)
        with open(os.path.join(self.d, "docs", "public-words-baseline.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_public_words_baselined_hit_is_silent(self):
        self._repo_with_doc("intro\nthe sauce is here\n")
        self.put_claude("This repo is public.\n")
        self._baseline("# accepted\nREADME.md\tsauce\tthe sauce is here\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}):
            self.assertEqual(ops_steps.public_words_gate(self.d), (True, []))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = ops_steps.main(["public-words", "--dir", self.d])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines()[0], "VERDICT: DONE")

    def test_public_words_baseline_entry_without_hit_is_stale_not_fail(self):
        self._repo_with_doc("intro\nthe sauce is here\n")
        self.put_claude("This repo is public.\n")
        self._baseline("README.md\tsauce\tthe sauce is here\nREADME.md\tsauce\tgone line\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}):
            self.assertEqual(ops_steps.public_words_gate(self.d), (True, ["stale: README.md\tsauce\tgone line"]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = ops_steps.main(["public-words", "--dir", self.d])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines()[0], "VERDICT: DONE")
        self.assertIn("stale: README.md\tsauce\tgone line", out.getvalue())

    def test_public_words_tracked_symlink_to_outside_file_is_not_a_hit(self):
        self._repo_with_doc("intro\n")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = os.path.join(os.path.realpath(outside.name), "secret.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("the sauce is here\n")
        os.symlink(secret, os.path.join(self.d, "link.txt"))
        git(self.d, "add", "link.txt")
        git(self.d, "commit", "-q", "-m", "link")
        self.put_claude("This repo is public.\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}):
            self.assertEqual(ops_steps.public_words_gate(self.d), (True, []))

    def test_public_words_empty_setting_is_done_and_silent(self):
        self._repo_with_doc("the sauce is here\n")
        self.put_claude("This repo is public.\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": ""}):
            self.assertEqual(ops_steps.public_words_gate(self.d), (True, []))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = ops_steps.main(["public-words", "--dir", self.d])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines()[0], "VERDICT: DONE")
        self.assertEqual(out.getvalue().splitlines()[1:], [""])

    def _gh_says(self, stdout):
        """Patch only the gh call of ops_steps; git keeps running for real."""
        real_run = subprocess.run
        gh = unittest.mock.Mock(returncode=0, stdout=stdout)

        def fake_run(args, **kw):
            return gh if args[0] == "gh" else real_run(args, **kw)

        return unittest.mock.patch.object(ops_steps.subprocess, "run", side_effect=fake_run)

    def test_public_words_private_repo_is_not_checked(self):
        self._repo_with_doc("the sauce is here\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}), self._gh_says('{"isPrivate": true}'):
            self.assertEqual(ops_steps.public_words_gate(self.d), (True, []))

    def test_public_words_gh_says_public_is_checked(self):
        self._repo_with_doc("the sauce is here\n")
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}), self._gh_says('{"isPrivate": false}'):
            ok, lines = ops_steps.public_words_gate(self.d)
        self.assertFalse(ok)
        self.assertEqual(lines, ["README.md:1: sauce", "add to docs/public-words-baseline.txt: README.md\tsauce\tthe sauce is here"])

    def test_public_words_failed_ls_files_fails_closed(self):
        self._repo_with_doc("the sauce is here\n")
        self.put_claude("This repo is public.\n")
        failed = unittest.mock.Mock(returncode=128, stdout="")
        with (
            unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}),
            unittest.mock.patch.object(ops_steps.gitx, "try_run", return_value=failed),
        ):
            ok, lines = ops_steps.public_words_gate(self.d)
        self.assertFalse(ok)
        self.assertEqual(len(lines), 1)
        self.assertIn("scan failed", lines[0])

    def test_public_words_gh_error_with_words_fails_closed(self):
        self._repo_with_doc("the sauce is here\n")
        with (
            unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}),
            unittest.mock.patch.object(ops_steps.subprocess, "run", side_effect=OSError("gh missing")),
        ):
            self.assertEqual(ops_steps.public_words_gate(self.d), (False, ["repo visibility unknown"]))

    def test_public_words_gh_timeout_with_words_fails_closed(self):
        self._repo_with_doc("the sauce is here\n")
        timeout = subprocess.TimeoutExpired(cmd="gh", timeout=1)
        with (
            unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}),
            unittest.mock.patch.object(ops_steps.subprocess, "run", side_effect=timeout),
        ):
            self.assertEqual(ops_steps.public_words_gate(self.d), (False, ["repo visibility unknown"]))

    def test_public_words_gh_empty_stdout_with_words_fails_closed(self):
        # rc 0 with no isPrivate key must not read as public: visibility unknown
        self._repo_with_doc("the sauce is here\n")
        for out in ("", "{}"):
            with (
                self.subTest(stdout=out),
                self._gh_says(out),
                unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": "sauce"}),
            ):
                self.assertEqual(ops_steps.public_words_gate(self.d), (False, ["repo visibility unknown"]))

    def test_public_words_gh_error_with_empty_words_is_done_without_gh(self):
        self._repo_with_doc("the sauce is here\n")
        gh = unittest.mock.Mock(side_effect=OSError("gh missing"))
        with unittest.mock.patch.dict(os.environ, {"PIPELINE_PUBLIC_FORBIDDEN_WORDS": ""}), unittest.mock.patch.object(ops_steps.subprocess, "run", gh):
            self.assertEqual(ops_steps.public_words_gate(self.d), (True, []))
        gh.assert_not_called()

    def test_stage_is_required_for_the_stage_commands(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ops_steps.main(["push", "--dir", self.d])

    def put_claude(self, text):
        with open(os.path.join(self.d, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    unittest.main()
