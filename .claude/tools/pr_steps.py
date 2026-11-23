#!/usr/bin/env python3
"""pr_steps: draft PR and branch cleanup of a stage branch by script, no model.

    python3 -I /root/.claude/tools/pr_steps.py open|push|ready|cleanup|sweep|local-merge --dir WORKSPACE --stage S --name FEATURE_OR_STAGE

open: push the stage branch, open a draft PR against `branch_release` (reuses an open PR of the branch; no PR while the branch has no commit of its own).
push: plain push (never force), then open the PR when it is missing. ready: push, `gh pr ready`, then wait for the workflow runs of the head (`gh run list`); red runs or a timeout = BLOCKED naming them.
merge: wait for pending checks (`pr_checks_timeout`), then squash-merge the stage PR (open, ready, mergeable, checks green) with title from the `pr-title:` line of TASKS.md; anything else = BLOCKED, the lead merges by hand.
cleanup: after the release, delete the local (and remote) stage branch, prune, `wt.py clean`, switch to `branch_release` and fast-forward it from origin. Safe to re-run.
sweep: before the stage branch exists, squash-merge every open Dependabot PR whose checks are all green (red, pending and check-less PRs stay open, none is closed), `git fetch --prune origin`, fast-forward `branch_release` (never force). One line per PR.
version-bump: last phase only, before `ready`: bump patch (or the `release:` level of TASKS.md) in package.json, Cargo.toml, their lockfiles and CHANGELOG.md, commit `chore(release): vX.Y.Z` on the stage branch. release: after the merge, tag the landed commit vX.Y.Z (merged PR squash SHA; local-only repo: release branch head), push the tag, `gh release create`.
local-merge: squash-merge the stage branch into `branch_release` in every local-only repo (main repo without `origin`, nested repos); subject from `.work/<feature>/SUBJECT` line 1, else `chore(<feature>): squash stage branch <branch>`. Dirty tree or conflict = BLOCKED (tree left clean). Safe to re-run, never pushes, keeps the branch.
First output line: `VERDICT: DONE` or `VERDICT: BLOCKED`."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline.pr_ops import Blocked, run  # noqa: F401
from pipeline.util import add_common_args


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["open", "push", "ready", "merge", "cleanup", "sweep", "local-merge", "version-bump", "release"])
    add_common_args(ap, stage=True)
    ap.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    v, text = run(a.what, a.dir, a.stage, a.name)
    print(f"VERDICT: {v}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
