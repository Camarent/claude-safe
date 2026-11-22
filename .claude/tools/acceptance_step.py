#!/usr/bin/env python3
"""acceptance_step: run the Plan.md acceptance commands from a fresh worktree of the stage branch, no model.

    python3 -I /root/.claude/tools/acceptance_step.py --dir WORKSPACE --stage S --name FEATURE_OR_STAGE

Writes .work/<stage>/acceptance.md (PASS/FAIL/MANUAL per checklist line), removes the worktree, prints `VERDICT: DONE` or `VERDICT: FAIL` and the lines."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline.acceptance import claude_reviewer, run
from pipeline.util import add_common_args


def main(argv=None):
    ap = argparse.ArgumentParser()
    add_common_args(ap, stage=True)
    ap.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    d = os.path.abspath(a.dir)
    v, text = run(d, a.stage, a.name, reviewer=claude_reviewer(d, a.stage))
    print(f"VERDICT: {v}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
