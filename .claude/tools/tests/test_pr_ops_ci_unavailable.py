"""`ci_available` false: pr-ready and master-merged wait for no CI run and merge on the green local gates, and the PR says so."""

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_base, pr_ops
from tests.support.gitrepo import git

CFG = {
    "gates": [{"name": "check", "cmd": "scripts/dev.sh check"}, {"name": "ccn", "cmd": "scripts/dev.sh ccn"}],
    "gates_merge": [{"name": "test", "cmd": "scripts/dev.sh test"}],
    "gates_final": [{"name": "sec", "cmd": "scripts/dev.sh sec"}],
}
OFF = {**CFG, "ci_available": False}


def R(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Scripted:
    """Runner: the first key that prefixes the space-joined command wins; every command is recorded."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, args, cwd=None, timeout=120):
        line = " ".join(args)
        self.calls.append(line)
        for k, v in self.table.items():
            if line.startswith(k):
                return v
        return R(0)


def view(field, value):
    return R(0, json.dumps({field: value}))


OPEN_PR = {
    "gh pr view b --json state": view("state", "OPEN"),
    "gh pr view b --json isDraft": view("isDraft", False),
    "gh pr view b --json headRefOid": view("headRefOid", "sha-b"),
    "gh pr view b --json mergeable": view("mergeable", "MERGEABLE"),
    "gh repo view": R(0, json.dumps({"squashMergeAllowed": True})),
    "gh pr ready": R(0),
    "gh pr merge b": R(0),
}


class CiUnavailableReady(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        git(self.tmp.name, "init", "-q")

    def test_ready_marks_ready_and_skips_the_ci_wait(self):
        run = Scripted({**OPEN_PR, "gh run list": R(1, "", "must not be called")})
        with mock.patch.object(pr_base, "_run", run):
            note = pr_ops._ready(self.tmp.name, OFF, "b")
        self.assertEqual(note, "marked ready; CI unavailable by setting: merged on green local gates")
        self.assertFalse([c for c in run.calls if c.startswith("gh run list")])

    def test_red_local_gate_still_blocks_before_ready(self):
        def runner(args, cwd, timeout):
            return R(1 if args[-1] == "scripts/dev.sh ccn" else 0)

        with self.assertRaisesRegex(pr_ops.Blocked, "scripts/dev.sh ccn"):
            pr_ops._gates_gate(self.tmp.name, OFF, "stage/x", runner=runner)

    def test_ready_step_writes_the_ci_sentence_into_the_pr_body(self):
        run = Scripted(dict(OPEN_PR))
        with (
            mock.patch.object(pr_ops, "_no_push_note", return_value=""),
            mock.patch.object(pr_ops, "_gates_gate", return_value="gates"),
            mock.patch.object(pr_ops, "_pushed"),
            mock.patch.object(pr_ops, "_open_pr", return_value="opened"),
            mock.patch.object(pr_ops, "web_note", return_value=""),
            mock.patch.object(pr_ops, "skipped_notes", return_value=""),
            mock.patch.object(pr_ops, "append_body") as body,
            mock.patch.object(pr_base, "_run", run),
        ):
            text = pr_ops._pr_step("ready", self.tmp.name, self.tmp.name, OFF, "s", "b")
        body.assert_called_once_with(self.tmp.name, "b", "CI unavailable by setting: merged on green local gates")
        self.assertTrue(text.endswith("marked ready; CI unavailable by setting: merged on green local gates"))

    def test_default_ready_keeps_the_ci_wait(self):
        run = Scripted({**OPEN_PR, "gh run list": R(0, json.dumps([{"name": "ci", "status": "completed", "conclusion": "success"}]))})
        with mock.patch.object(pr_base, "_run", run):
            note = pr_ops._ready(self.tmp.name, CFG, "b")
        self.assertEqual(note, "marked ready; checks green")
        self.assertTrue([c for c in run.calls if c.startswith("gh run list")])


class CiUnavailableMerge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.clock = FakeClock()

    def merge(self, cfg, table):
        run = Scripted({**OPEN_PR, **table})
        with mock.patch.object(pr_base, "_run", run):
            note = pr_ops._merge(self.d, self.d, cfg, "s", "b", self.clock)
        return note, run

    def test_merge_skips_the_check_wait_and_says_so(self):
        note, run = self.merge(OFF, {"gh run list": R(1, "", "must not be called")})
        self.assertIn("PR squash-merged", note)
        self.assertIn("local gates of the pr-ready step relied on", note)
        self.assertEqual(self.clock.now, 0.0)
        self.assertFalse([c for c in run.calls if c.startswith("gh run list")])
        self.assertFalse([c for c in run.calls if "scripts/dev.sh" in c])
        merge = [c for c in run.calls if c.startswith("gh pr merge b")]
        self.assertEqual(len(merge), 1)
        self.assertIn("Squash-merged by the pipeline on green local gates (CI unavailable by setting).", merge[0])

    def test_merge_still_blocks_when_squash_is_forbidden(self):
        with self.assertRaisesRegex(pr_ops.Blocked, "allows no squash merge"):
            self.merge(OFF, {"gh repo view": R(0, json.dumps({"squashMergeAllowed": False}))})

    def test_default_merge_still_waits_for_ci(self):
        with self.assertRaisesRegex(pr_ops.CiReadError, "gh run list failed"):
            self.merge(CFG, {"gh run list": R(1, "", "HTTP 503")})


if __name__ == "__main__":
    unittest.main()
