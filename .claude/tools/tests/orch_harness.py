"""Shared fixtures for the orch phase tests (test_orch_phases_*.py): the PhaseHarness mixin and the module-level index patch."""

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

os.environ.setdefault("ORCH_NO_INDEX", "1")  # tests must not start background indexers
os.environ["PIPELINE_NO_LEADS"] = "1"
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, graph, pr_mode
from tests.support.tmpdirs import tracked_mkdtemp

_INDEX_PATCH = mock.patch.object(graph, "run_index", return_value=0)  # tests never run the real indexer


def start_index_patch():
    _INDEX_PATCH.start()


def stop_index_patch():
    _INDEX_PATCH.stop()


CHECKOUT = Path(__file__).resolve().parents[3]  # .claude/tools/tests -> the checkout root


def branch_refs():
    """-> the local branch refs of the checkout (read-only `git for-each-ref`), or None when git or the checkout is unavailable."""
    if not os.access(CHECKOUT, os.W_OK):  # read-only checkout: nothing a test could have written, nothing to compare
        return None
    try:
        return subprocess.run(
            ["git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"],
            cwd=CHECKOUT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):  # no git (release container): nothing to compare
        return None


def argparse_ns(d):
    import argparse

    return argparse.Namespace(dir=d, stage="s")


if TYPE_CHECKING:

    class _Mixin(unittest.TestCase):
        pass

else:

    class _Mixin:
        pass


class PhaseHarness(_Mixin):
    """merge per wave, tests, docs, audits, one fix cycle, retry/skip: setUp and helpers of the PhaseTest groups (mixed into a TestCase)."""

    _refs_before: str | None = None  # set by setUpClass: branch refs of the checkout before the class ran

    @classmethod
    def setUpClass(cls):
        cls._refs_before = branch_refs()

    @classmethod
    def tearDownClass(cls):
        # a test must not create or move a branch of the real checkout: the refs must read the same after the class
        if cls._refs_before is None:
            return
        after = branch_refs()
        assert after == cls._refs_before, f"branch refs of the checkout changed during the tests:\n{cls._refs_before}---\n{after}"

    def setUp(self):
        for name, val in (
            ("has_origin", True),
            ("pr_active", False),
            ("gh_down", False),
            ("direct_flow", True),
            ("sweep_active", False),
        ):  # legacy flow: an origin, no PR; tests never call gh
            pa = mock.patch.object(pr_mode, name, return_value=val)
            pa.start()
            self.addCleanup(pa.stop)
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/.work/s/reports")
        Path(f"{self.d}/pipeline.json").write_text(
            '{"test_env": {"DOJO_KIT_ROOT": "."}, "branch_work": "dev", "qualityrun": false, "testrun": false}', encoding="utf-8"
        )
        Path(f"{self.d}/AUDIT.md").write_text(
            "```ux\nserve: x\nurl: http://127.0.0.1:1/\n```\n", encoding="utf-8"
        )  # UX review runs only when the project has a ux block
        with open(f"{self.d}/.work/s/TASKS.md", "w", encoding="utf-8") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n| task-1 | a | dev | 1 | wave 1, haiku |\n\n## Graph\nwave 1: task-1\nflags: ui docs no-retro\n"
            )

    def go(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_orch.main([args[0], "--stage", "s", "--dir", self.d, *args[1:]])
        return json.loads(out.getvalue())

    def batch(self):
        r = self.go("next")
        self.assertEqual(r["action"], "dispatch", r)
        return r["dispatches"]

    def batch_agents(self):
        """Next batch of model agents: re-audit context scripts (audit-context-N, plan-vs-changes-N) are answered first."""
        ds = self.batch()
        while [x["agent"] for x in ds] == ["script"]:
            self.answer(ds[0], "VERDICT: DONE")
            ds = self.batch()
        return ds

    def answer(self, d, text):
        Path(f"{self.d}/.work/s/reports/report-{d['id']}.md").write_text(text, encoding="utf-8")
        return self.go("report", "--id", d["id"])

    def run_batch(self, expect_agents, *verdicts):
        ds = self.batch()
        if sorted(expect_agents) == [
            "tester",
            "web-tester",
        ]:  # the browser check may run in the same batch or after the test step: only both having run is the contract
            seen, first = [], ds
            while True:
                for x in ds:
                    self.answer(x, "VERDICT: PASS")
                    seen.append(x["agent"])
                if "web-tester" in seen:
                    break
                ds = self.batch()
            return first
        if "ux-reviewer" in expect_agents and [x["agent"] for x in ds] == ["script"]:  # screenshots by script come first
            self.assertIn("ux_run.py", ds[0]["prompt"])
            self.answer(ds[0], "VERDICT: DONE")
            ds = self.batch()
        while "script" not in expect_agents and [x["agent"] for x in ds] == [
            "script"
        ]:  # re-audit context scripts (audit-context-N, plan-vs-changes-N) come first
            self.answer(ds[0], "VERDICT: DONE")
            ds = self.batch()
        self.assertEqual(sorted(x["agent"] for x in ds), sorted(expect_agents))
        for x, v in zip(sorted(ds, key=lambda x: x["agent"]), verdicts, strict=False):
            self.answer(x, f"VERDICT: {v}")
        return ds

    def base(self):
        self.run_batch(["dev"], "DONE")
        self.run_batch(["reviewer"], "PASS")

    def audited(self):
        self.base()
        self.run_batch(["merger"], "DONE")
        t = self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.assertIn("DOJO_KIT_ROOT", t[0]["prompt"])
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["arch-auditor", "sec-auditor", "ux-reviewer"], "CLEAN", "FIXES", "CLEAN")  # one first pass

    def plan_fix_tasks(self):
        p = self.batch()
        self.assertEqual(p[0]["agent"], "architect")
        self.assertIn("audit-sec.md", p[0]["prompt"])
        self.assertNotIn("audit-arch.md", p[0]["prompt"])
        self.assertIn("wave 2:", p[0]["prompt"])
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("| task-2 | fix | dev(sonnet) | 1 | wave 2, sonnet |\nwave 2: task-2\n")  # what architect writes
        self.answer(p[0], "VERDICT: DONE")

    def plan_round2(self, name):
        p = self.batch()
        self.assertEqual(p[0]["agent"], "architect")
        self.assertIn(name, p[0]["prompt"])
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("| task-3 | fix | dev(sonnet) | 1 | wave 3, sonnet |\nwave 3: task-3\n")
        self.answer(p[0], "VERDICT: DONE")

    def tailpost(self):
        """Default post steps: lead merged dev->master (skip), push-master, index-refresh."""
        self.go("skip", "--step", "master-merged")
        self.run_batch(["ops"], "DONE")
        self.run_batch(["script"], "DONE")
        self.run_batch(["script"], "DONE")  # index-refresh, then cleanup

    def post(self, extra):
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write(extra)
        self.base()
        self.run_batch(["merger"], "DONE")
        self.run_batch(["tester", "web-tester"], "PASS", "PASS")
        self.run_batch(["docs-writer"], "DONE")
        self.run_batch(["script"], "DONE")  # plan-vs-changes: recall for every tested stage
        self.run_batch(["script"], "DONE")
        self.run_batch(["arch-auditor", "sec-auditor", "ux-reviewer"], "CLEAN", "CLEAN", "CLEAN")  # one first pass

    def plan_fix_tasks_for(self, name):
        p = self.batch()
        self.assertIn(name, p[0]["prompt"])
        with open(f"{self.d}/.work/s/TASKS.md", "a", encoding="utf-8") as f:
            f.write("| task-2 | fix | dev | 1 | wave 2, haiku |\nwave 2: task-2\n")
        self.answer(p[0], "VERDICT: DONE")
