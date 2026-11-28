"""cs-wave run on a stage whose PR is merged: closed out with `done:` and exit 0 before the plan is read or the steps run; a gh failure, no PR or an open PR keeps the old behaviour."""

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, cli_wave, driver, pr_base
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

NO_TASKS_BLOCK = "blocked: no TASKS.md"


def gh_reply(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


class WaveResumeMergedPrTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.w = f"{self.d}/.work/s"
        os.makedirs(self.w)
        Path(self.w, "Plan.md").write_text("# plan\n", encoding="utf-8")  # stale: no TASKS.md, the old run would block here
        self.calls = []
        self._o, self._i, self._c = driver.orch, driver.orch_inproc, claude.run_claude
        driver.orch, driver.orch_inproc, claude.run_claude = self.fake_orch, self.fake_orch, self.fake_claude

    def tearDown(self):
        driver.orch, driver.orch_inproc, claude.run_claude = self._o, self._i, self._c

    def fake_orch(self, cmd, stage, dir, *extra):
        raise AssertionError(f"driver called: {cmd}")

    def fake_claude(self, *args, **kwargs):
        raise AssertionError("a step ran")

    def fake_gh(self, reply):
        def run(args, cwd, timeout=None):
            self.calls.append(args)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return run

    def run_w(self, gh):
        out = io.StringIO()
        with (
            mock.patch.object(pr_base, "_run", gh),
            mock.patch.object(cli_wave, "run_loop", side_effect=AssertionError("loop ran")) as loop,
            redirect_stdout(out),
        ):
            rc = cli_wave.main(["run", "--stage", "s", "--dir", self.d])
        loop.assert_not_called()
        return rc, out.getvalue()

    def test_merged_pr_closes_out_with_exit_0_and_skips_the_stale_plan(self):
        rc, out = self.run_w(self.fake_gh(gh_reply('[{"state": "MERGED"}]')))
        self.assertEqual(rc, 0)
        self.assertIn("done: PR of stage/s is merged: stage closed out", out)
        self.assertNotIn("brief unreadable", out)
        self.assertNotIn(NO_TASKS_BLOCK, out)
        self.assertEqual(self.calls[0][:4], ["gh", "pr", "list", "--head"])
        self.assertIn("--state", self.calls[0])

    def test_gh_failure_keeps_the_old_behaviour(self):
        rc, out = self.run_w(self.fake_gh(gh_reply("", returncode=1)))
        self.assertEqual(rc, 4)
        self.assertIn(NO_TASKS_BLOCK, out)

    def test_gh_missing_keeps_the_old_behaviour(self):
        rc, out = self.run_w(self.fake_gh(pr_base.Blocked("gh: not found")))
        self.assertEqual(rc, 4)
        self.assertIn(NO_TASKS_BLOCK, out)

    def test_newest_pr_open_keeps_the_old_behaviour(self):
        reply = gh_reply('[{"state": "MERGED", "createdAt": "2026-01-01"}, {"state": "OPEN", "createdAt": "2026-02-01"}]')
        rc, out = self.run_w(self.fake_gh(reply))
        self.assertEqual(rc, 4)
        self.assertNotIn("stage closed out", out)

    def test_branch_tip_past_the_merged_head_keeps_the_old_behaviour(self):
        reply = gh_reply('[{"state": "MERGED", "createdAt": "2026-01-01", "headRefOid": "aaa"}]')
        with mock.patch.object(pr_base, "_git", return_value=gh_reply("bbb\n")):
            rc, out = self.run_w(self.fake_gh(reply))
        self.assertEqual(rc, 4)
        self.assertNotIn("stage closed out", out)

    def test_branch_tip_equal_to_the_merged_head_closes_out(self):
        reply = gh_reply('[{"state": "MERGED", "createdAt": "2026-01-01", "headRefOid": "aaa"}]')
        with mock.patch.object(pr_base, "_git", return_value=gh_reply("aaa\n")):
            rc, out = self.run_w(self.fake_gh(reply))
        self.assertEqual(rc, 0)
        self.assertIn("stage closed out", out)

    def test_no_pr_or_open_pr_keeps_the_old_behaviour(self):
        for reply in (gh_reply("[]"), gh_reply('[{"state": "OPEN"}]'), gh_reply("not json")):
            with self.subTest(reply=reply.stdout):
                rc, out = self.run_w(self.fake_gh(reply))
                self.assertEqual(rc, 4)
                self.assertIn(NO_TASKS_BLOCK, out)
                self.assertNotIn("stage closed out", out)
