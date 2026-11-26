"""deps-sweep: fake `gh` on PATH, bare scratch origin; green Dependabot PR merged, red and pending untouched."""

import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_mode, pr_ops
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp

FAKE_GH = """#!/bin/sh
echo "$@" >> "$GH_LOG"
case "$1 $2" in
  "auth status") exit 0 ;;
  "pr list") cat "$GH_LOG.prs" ;;
  "pr merge")
    [ -n "$GH_ORIGIN_CLONE" ] && { git -C "$GH_ORIGIN_CLONE" commit -q --allow-empty -m "squash $3" && git -C "$GH_ORIGIN_CLONE" push -q origin master; }
    exit 0 ;;
esac
"""

PRS = """[{"number": 1, "title": "bump a", "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}]},
{"number": 2, "title": "bump b", "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}]},
{"number": 3, "title": "bump c", "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}]}]"""


class GhScratch(unittest.TestCase):
    """Scratch repo plus a fake `gh` on PATH; subclasses add the tests (this class has none)."""

    def setUp(self):
        self.root = tracked_mkdtemp()
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
        Path(self.log + ".prs").write_text(PRS, encoding="utf-8")
        self.old = dict(os.environ)
        os.environ["PATH"] = bin_ + os.pathsep + os.environ["PATH"]
        os.environ["GH_LOG"] = self.log
        Path(self.d, "pipeline.json").write_text("{}", encoding="utf-8")
        self.a = argparse.Namespace(dir=self.d, stage="s", dry_run=False)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def add_origin(self):
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", self.origin], check=True)
        git(self.d, "remote", "add", "origin", self.origin)
        git(self.d, "push", "-q", "origin", "master")

    def calls(self):
        return Path(self.log).read_text().splitlines() if os.path.exists(self.log) else []


class SweepTest(GhScratch):
    def test_green_merged_red_pending_left_and_master_fast_forwarded(self):
        self.add_origin()
        other = os.path.join(self.root, "other")
        subprocess.run(["git", "clone", "-q", self.origin, other], check=True)
        os.environ["GH_ORIGIN_CLONE"] = other
        self.assertTrue(pr_mode.sweep_active(self.a))
        v, text = pr_ops.run("sweep", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        self.assertIn("#1 merged", text)
        self.assertIn("#2 skipped red", text)
        self.assertIn("#3 skipped pending", text)
        self.assertIn("1 merged, 1 skipped red, 1 skipped pending", text)
        merges = [c for c in self.calls() if c.startswith("pr merge")]
        self.assertEqual(merges, ["pr merge 1 --squash --delete-branch"])
        self.assertFalse([c for c in self.calls() if c.startswith("pr close")])
        self.assertEqual(git(self.d, "rev-parse", "HEAD"), git(other, "rev-parse", "HEAD"))

    def test_fast_forward_when_work_branch_checked_out(self):
        self.add_origin()
        other = os.path.join(self.root, "other")
        subprocess.run(["git", "clone", "-q", self.origin, other], check=True)
        os.environ["GH_ORIGIN_CLONE"] = other
        git(self.d, "switch", "-q", "-c", "stage/x")
        pr_ops.run("sweep", self.d, "s", "x")
        self.assertEqual(git(self.d, "rev-parse", "master"), git(other, "rev-parse", "HEAD"))
        self.assertEqual(git(self.d, "symbolic-ref", "--short", "HEAD"), "stage/x")

    def test_no_origin_no_step(self):
        self.assertFalse(pr_mode.sweep_active(self.a))

    def test_opt_out_no_step(self):
        self.add_origin()
        Path(self.d, "pipeline.json").write_text('{"deps_sweep": false}', encoding="utf-8")
        self.assertFalse(pr_mode.sweep_active(self.a))

    def test_fixed_branch_work_no_step(self):
        self.add_origin()
        Path(self.d, "pipeline.json").write_text('{"branch_work": "dev"}', encoding="utf-8")
        self.assertFalse(pr_mode.sweep_active(self.a))


# A token without checks:read: every call that reads check runs fails with the GraphQL error; workflow runs stay readable.
FAKE_GH_NO_CHECKS = """#!/bin/sh
echo "$@" >> "$GH_LOG"
deny() { echo "GraphQL: Resource not accessible by personal access token (statusCheckRollup)" >&2; exit 1; }
case "$1 $2" in
  "auth status") exit 0 ;;
  "pr list") case "$*" in *statusCheckRollup*) deny ;; *) cat "$GH_LOG.prs" ;; esac ;;
  "pr checks") deny ;;
  "pr view") echo '{"headRefOid": "sha-ok"}' ;;
  "run list")
    case "$*" in
      *sha-ok*) echo '[{"name": "ci", "status": "completed", "conclusion": "success"}]' ;;
      *sha-red*) echo '[{"name": "ci", "status": "completed", "conclusion": "failure"}]' ;;
      *sha-wait*) echo '[{"name": "ci", "status": "in_progress", "conclusion": ""}]' ;;
      *) echo '[]' ;;
    esac ;;
  "pr merge") exit 0 ;;
esac
"""

PRS_NO_CHECKS = """[{"number": 1, "title": "bump a", "headRefOid": "sha-ok"},
{"number": 2, "title": "bump b", "headRefOid": "sha-red"},
{"number": 3, "title": "bump c", "headRefOid": "sha-wait"},
{"number": 4, "title": "bump d", "headRefOid": "sha-none"}]"""


class NoChecksTokenTest(GhScratch):
    """Same scratch setup, but gh answers like a fine-grained token."""

    def setUp(self):
        super().setUp()
        Path(self.root, "bin", "gh").write_text(FAKE_GH_NO_CHECKS, encoding="utf-8")
        Path(self.log + ".prs").write_text(PRS_NO_CHECKS, encoding="utf-8")

    def test_sweep_reads_workflow_runs(self):
        self.add_origin()
        v, text = pr_ops.run("sweep", self.d, "s", "x")
        self.assertEqual(v, "DONE", text)
        self.assertIn("#1 merged", text)
        self.assertIn("#2 skipped red", text)
        self.assertIn("#3 skipped pending", text)
        self.assertIn("#4 skipped pending", text)
        self.assertEqual([c for c in self.calls() if c.startswith("pr merge")], ["pr merge 1 --squash --delete-branch"])

    def test_pr_checks_green_via_runs(self):
        c = pr_ops._pr_checks(self.d, "stage/x", 5)
        self.assertEqual(c.returncode, 0)

    def test_pr_checks_plain_green_via_runs(self):
        self.assertEqual(pr_ops._pr_checks(self.d, "stage/x").returncode, 0)

    def test_failing_names_via_runs(self):
        Path(self.root, "bin", "gh").write_text(FAKE_GH_NO_CHECKS.replace('sha-ok"}', 'sha-red"}'), encoding="utf-8")
        self.assertEqual(pr_ops._failing(self.d, "stage/x"), ["ci"])
        self.assertEqual(pr_ops._pr_checks(self.d, "stage/x").returncode, 1)

    def test_no_workflows_means_no_checks_reported(self):
        Path(self.root, "bin", "gh").write_text(FAKE_GH_NO_CHECKS.replace('sha-ok"}', 'sha-none"}'), encoding="utf-8")
        c = pr_ops._pr_checks(self.d, "stage/x", 5)
        self.assertEqual((c.returncode, c.stdout), (1, "no checks reported"))

    def test_unrelated_gh_error_is_not_swallowed(self):
        Path(self.root, "bin", "gh").write_text('#!/bin/sh\necho "boom" >&2\nexit 1\n', encoding="utf-8")
        with self.assertRaises(pr_ops.CiReadError):  # no head sha: the CI read fails, never "no checks"
            pr_ops._pr_checks(self.d, "stage/x")


# Checks are read from the workflow runs only: `gh pr checks` must never be called (it needs checks:read).
FAKE_GH_RUNS = """#!/bin/sh
echo "$@" >> "$GH_LOG"
case "$1 $2" in
  "auth status") exit 0 ;;
  "pr checks") echo "pr checks must not be called" >&2; exit 1 ;;
  "pr view") echo '{"headRefOid": "sha-head"}' ;;
  "run list") cat "$GH_LOG.runs" ;;
esac
"""


class CiReadsRunsTest(GhScratch):
    """Each check state comes from `gh run list`; the checks command stays uncalled."""

    def setUp(self):
        super().setUp()
        Path(self.root, "bin", "gh").write_text(FAKE_GH_RUNS, encoding="utf-8")

    def runs(self, text):
        Path(self.log + ".runs").write_text(text, encoding="utf-8")

    def test_green_red_pending_and_none(self):
        cases = {
            "green": ('[{"name": "ci", "status": "completed", "conclusion": "success"}]', (0, "")),
            "red": ('[{"name": "ci", "status": "completed", "conclusion": "failure"}]', (1, "ci")),
            "pending": ('[{"name": "ci", "status": "in_progress", "conclusion": ""}]', (pr_ops.GH_CHECKS_PENDING_RC, "pending")),
            "none": ("[]", (1, "no checks reported")),
        }
        for name, (runs, want) in cases.items():
            with self.subTest(name):
                self.runs(runs)
                c = pr_ops._pr_checks(self.d, "stage/x")
                self.assertEqual((c.returncode, c.stdout), want)
        self.assertFalse([x for x in self.calls() if x.startswith("pr checks")])


if __name__ == "__main__":
    unittest.main()
