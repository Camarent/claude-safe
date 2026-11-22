"""run_summary: stage tip, PR state from an injected gh runner, last events of orch.jsonl. No network, no real git."""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_summary
from pipeline.configuration import ORCH_LOG, WORK_DIR
from tests.support.tmpdirs import tracked_mkdtemp

STAGE = "s1"
PR_JSON = json.dumps([{"number": 7, "state": "OPEN", "url": "https://example.test/pull/7", "isDraft": False}])


class FakeGh:
    """gh runner stand-in: returns (rc, out) and records every args list it was called with."""

    def __init__(self, rc: int | None = 0, out: str = PR_JSON):
        self.rc = rc
        self.out = out
        self.seen: list[list[str]] = []

    def __call__(self, args):
        self.seen.append(args)
        return self.rc, self.out


def fake_gh(rc: int | None = 0, out: str = PR_JSON) -> FakeGh:
    return FakeGh(rc, out)


def fake_git(sha: str | None = "abc1234\n"):
    def git(repo, *args):  # the runner signature of gitx.try_out; the fake ignores the repo and the args
        return sha

    return git


class RunSummaryTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.stage_dir = os.path.join(self.d, WORK_DIR, STAGE)
        os.makedirs(self.stage_dir)
        self.log = os.path.join(self.stage_dir, ORCH_LOG)

    def write_events(self, n):
        with open(self.log, "w", encoding="utf-8") as f:
            for i in range(1, n + 1):
                f.write(json.dumps({"i": i, "ev": "report"}) + "\n")

    def test_tip_pr_and_last_three_events(self):
        self.write_events(5)
        gh = fake_gh()
        lines = run_summary.summary(self.d, STAGE, gh, fake_git())
        self.assertEqual(lines[0], "stage branch stage/s1: abc1234")
        self.assertEqual(lines[1], "PR: #7 OPEN https://example.test/pull/7")
        events = lines[2:]
        self.assertEqual(len(events), 3)
        self.assertEqual([json.loads(e.removeprefix("event: "))["i"] for e in events], [3, 4, 5])
        self.assertEqual(gh.seen, [["pr", "list", "--head", "stage/s1", "--state", "all", "--json", run_summary.GH_PR_FIELDS]])

    def test_missing_orch_log_prints_clear_line(self):
        lines = run_summary.summary(self.d, STAGE, fake_gh(), fake_git())
        self.assertEqual(lines[2], f"events: no {ORCH_LOG} for this stage yet")

    def test_empty_orch_log(self):
        self.write_events(0)
        lines = run_summary.summary(self.d, STAGE, fake_gh(), fake_git())
        self.assertEqual(lines[2], f"events: {ORCH_LOG} has no events")

    def test_gh_missing_or_failing(self):
        self.write_events(1)
        self.assertEqual(run_summary.summary(self.d, STAGE, fake_gh(rc=None, out=""), fake_git())[1], "PR: gh not available")
        self.assertEqual(run_summary.summary(self.d, STAGE, fake_gh(rc=4, out=""), fake_git())[1], "PR: gh failed (rc 4)")
        self.assertEqual(run_summary.summary(self.d, STAGE, fake_gh(out="not json"), fake_git())[1], "PR: gh gave unreadable output")

    def test_no_pr_and_draft_pr(self):
        self.write_events(1)
        self.assertEqual(run_summary.summary(self.d, STAGE, fake_gh(out="[]"), fake_git())[1], "PR: none for stage/s1")
        draft = json.dumps([{"number": 9, "state": "OPEN", "url": "u", "isDraft": True}])
        self.assertEqual(run_summary.summary(self.d, STAGE, fake_gh(out=draft), fake_git())[1], "PR: #9 OPEN draft u")

    def test_branch_not_found(self):
        self.write_events(1)
        lines = run_summary.summary(self.d, STAGE, fake_gh(), fake_git(sha=None))
        self.assertEqual(lines[0], "stage branch stage/s1: not found")

    def test_main_exits_zero_and_prints_lines(self):
        self.write_events(4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_summary.main(["--stage", STAGE, "--dir", self.d], fake_gh(), fake_git())
        self.assertEqual(rc, 0)
        printed = out.getvalue().splitlines()
        self.assertEqual(printed[0], "stage branch stage/s1: abc1234")
        self.assertEqual(len(printed), 2 + 3)


if __name__ == "__main__":
    unittest.main()
