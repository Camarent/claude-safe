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


class SweepTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
