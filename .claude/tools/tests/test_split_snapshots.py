"""Snapshots that prove the steps/briefs/phases split changed nothing: dry-run step lists of fixed stages and the dev/reviewer brief text.
Regenerate with SNAPSHOT_UPDATE=1 (only for an intended behaviour change)."""

import json
import os
import re
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import briefs
from tests.support.project import dry_run, make_project

SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", "split.json")
UX = "# audit\n\n```ux\nbuild: true\nserve: true\nurl: http://x\n```\n"
CASES: dict[str, dict] = {
    "minimal": {"flags": "no-retro"},
    "no-audit": {"flags": "no-retro no-audit"},
    "ui": {"flags": "no-retro ui", "audit_md": UX},
    "retro": {"flags": ""},
    "gates": {"flags": "no-retro", "config": {"gates": [{"name": "t", "cmd": "true"}]}},
    "pre-approved": {"flags": "no-retro", "extra_lines": "PRE-APPROVED: master merge\n"},
}


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # the checkout differs per worktree


def _norm(x, d):
    text = json.dumps(x).replace(json.dumps(d)[1:-1], "<D>").replace(json.dumps(ROOT)[1:-1], "<R>")
    return json.loads(re.sub(r"[\w/.-]*/wave-dry-\w+", "<W>", text))


def _short(res):
    """The walker cuts long lines by the temp path length; keep the stable head of each."""
    rc, st, last = res
    return [rc, [[a, b, c, rest[:40]] for a, b, c, rest in st], last[:40]]


def _briefs(d):
    class A:
        stage = "s"
        dir = d

    task = {"id": "task-1"}
    info = {"branch": "job/t1", "base": "abc", "repos": {"r": "/w/r"}}
    with mock.patch.dict(os.environ, {"PIPELINE_NO_LEADS": "1"}):
        out = {
            "dev": briefs.prompt_for(A, task, "dev", None, "x1"),
            "dev-rework": briefs.prompt_for(A, task, "dev", "rw.md", "x2"),
            "reviewer": briefs.prompt_for(A, task, "reviewer", None, "x3"),
        }
        with mock.patch("pipeline.briefs.job_info", return_value=info), mock.patch("pipeline.briefs.job_diff", return_value="DIFF"):
            out["dev-wt"] = briefs.prompt_for(A, task, "dev", None, "x4")
            out["reviewer-wt"] = briefs.prompt_for(A, task, "reviewer", None, "x5")
    return out


def _collect():
    out = {}
    for name, kw in CASES.items():
        d = make_project(**kw)
        try:
            out["dry:" + name] = _short(_norm(list(dry_run(d)), d))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    d = make_project("no-retro")
    try:
        out["brief"] = _norm(_briefs(d), d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


class SplitSnapshotTest(unittest.TestCase):
    def test_snapshots_unchanged(self):
        cur = _collect()
        if os.environ.get("SNAPSHOT_UPDATE"):
            with open(SNAP, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=1, sort_keys=True)
        with open(SNAP, encoding="utf-8") as f:
            want = json.load(f)
        for k in sorted(want):
            self.assertEqual(cur[k], want[k], k)
        self.assertEqual(sorted(cur), sorted(want))


if __name__ == "__main__":
    unittest.main()
