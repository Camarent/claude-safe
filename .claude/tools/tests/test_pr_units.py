"""pr_steps.py helpers and pipeline.pr_mode with a fake command runner (no git remote, no gh, no network)."""

import contextlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pr_steps
from pipeline import pr_base, pr_mode, pr_ops

_GIT_ROUTE: Any


def setUpModule():
    """git calls of pr_ops go through gitx; the fakes below stand in for pr_ops._run, so route gitx.run there (as ["git", ...])."""
    global _GIT_ROUTE
    _GIT_ROUTE = mock.patch.object(pr_ops.gitx, "run", lambda repo, *a, timeout=60: pr_base._run(["git", *a], repo, timeout))
    _GIT_ROUTE.start()


def tearDownModule():
    _GIT_ROUTE.stop()


def R(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def fake(table):
    """Runner: the first table key that is a prefix of the space-joined command wins; default R(0)."""

    def run(args, cwd=None, timeout=120):
        line = " ".join(args)
        for k, v in table.items():
            if line.startswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        return R(0)

    return run


class PrStepsHelpers(unittest.TestCase):
    def test_run_maps_timeout_and_oserror_to_blocked(self):
        with (
            mock.patch.object(pr_ops.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)),
            self.assertRaisesRegex(pr_ops.Blocked, "timed out after 5s"),
        ):
            pr_base._run(["git", "a", "b", "c", "d"], ".", 5)
        with (
            mock.patch.object(pr_ops.subprocess, "run", side_effect=FileNotFoundError("no gh")),
            self.assertRaisesRegex(pr_ops.Blocked, "^gh: .*no gh"),
        ):
            pr_base._run(["gh", "x"], ".")

    def test_pushed_failure_quotes_the_error(self):
        with (
            mock.patch.object(pr_base, "_run", fake({"git": R(1, "", "rejected")})),
            self.assertRaisesRegex(pr_ops.Blocked, "push of b failed: rejected"),
        ):
            pr_ops._pushed(".", "b")
        with mock.patch.object(pr_base, "_run", fake({})):
            pr_ops._pushed(".", "b")

    def test_pushed_long_error_keeps_head_and_tail(self):
        err = "HEAD" + "x" * 196 + "-" * 5000 + "y" * 596 + "TAIL"
        with mock.patch.object(pr_base, "_run", fake({"git": R(1, "", err)})), self.assertRaises(pr_ops.Blocked) as cm:
            pr_ops._pushed(".", "b")
        msg = str(cm.exception)
        self.assertIn(err[:200], msg)
        self.assertIn(err[-600:], msg)
        self.assertNotIn("-" * 100, msg)

    def test_open_pr_variants(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        cfg = {"branch_release": "master"}
        with (
            mock.patch.object(pr_base, "_run", fake({"gh pr list": R(1, "", "denied")})),
            self.assertRaisesRegex(pr_ops.Blocked, "gh pr list failed: denied"),
        ):
            pr_ops._open_pr(d, ".", cfg, "s", "br")
        with mock.patch.object(pr_base, "_run", fake({"gh pr list": R(0, json.dumps([{"number": 7, "url": "u"}]))})):
            self.assertEqual(pr_ops._open_pr(d, ".", cfg, "s", "br"), "PR #7 reused")
        with mock.patch.object(pr_base, "_run", fake({"gh pr list": R(0, "garbage"), "git rev-list": R(0, "0\n")})):
            self.assertIn("no commit beyond the release branch", pr_ops._open_pr(d, ".", cfg, "s", "br"))
        os.makedirs(f"{d}/.work/s")
        with open(f"{d}/.work/s/TASKS.md", "w") as f:
            f.write("\n# Stage S title\nrest\n")
        calls = []
        base = fake({"gh pr list": R(0, "[]"), "git rev-list": R(0, "3\n"), "gh pr create": R(0, "x\nhttps://pr/1\n")})

        def spy(args, cwd=None, timeout=120):
            calls.append(args)
            return base(args, cwd, timeout)

        with mock.patch.object(pr_base, "_run", spy):
            self.assertEqual(pr_ops._open_pr(d, ".", cfg, "s", "br"), "PR opened: https://pr/1")
        self.assertEqual(calls[-1][calls[-1].index("--title") + 1], "Stage S title")
        with (
            mock.patch.object(pr_base, "_run", fake({"gh pr list": R(0, "[]"), "git rev-list": R(1), "gh pr create": R(1, "", "nope")})),
            self.assertRaisesRegex(pr_ops.Blocked, "gh pr create failed: nope"),
        ):
            pr_ops._open_pr(d, ".", cfg, "s", "br")
        with mock.patch.object(pr_base, "_run", fake({"gh pr list": R(0, "[]"), "git rev-list": R(1), "gh pr create": R(0, "")})):
            self.assertEqual(pr_ops._open_pr(d, ".", cfg, "s", "br"), "PR opened: br")

    def test_view_and_failing_tolerate_bad_json(self):
        with mock.patch.object(pr_base, "_run", fake({"gh pr view": R(0, '{"state": "MERGED"}')})):
            self.assertEqual(pr_ops._view(".", "b", "state"), "MERGED")
        for out in (R(1, "{}"), R(0, "nope"), R(0, "[1]")):
            with mock.patch.object(pr_base, "_run", fake({"gh pr view": out})):
                self.assertIsNone(pr_ops._view(".", "b", "state"))
        runs = json.dumps(
            [
                {"name": "a", "status": "completed", "conclusion": "failure"},
                {"name": "b", "status": "completed", "conclusion": "success"},
                {"name": "c", "status": "completed", "conclusion": "cancelled"},
            ]
        )
        with mock.patch.object(pr_base, "_run", fake({"gh pr view": R(0, '{"headRefOid": "sha"}'), "gh run list": R(0, runs)})):
            self.assertEqual(pr_ops._failing(".", "b"), ["a", "c"])
        for out in ('[{"x": 1}]', "[5]"):
            with mock.patch.object(pr_base, "_run", fake({"gh pr view": R(0, '{"headRefOid": "sha"}'), "gh run list": R(0, out)})):
                self.assertEqual(pr_ops._failing(".", "b"), [])
        for out in (R(0, "nope"), R(0, "{}"), R(1, "")):  # a failed or unreadable run list is an error, never "no failures"
            with (
                mock.patch.object(pr_base, "_run", fake({"gh pr view": R(0, '{"headRefOid": "sha"}'), "gh run list": out})),
                self.assertRaises(pr_ops.CiReadError),
            ):
                pr_ops._failing(".", "b")

    def test_landed(self):
        with mock.patch.object(pr_base, "_view", return_value="MERGED"):
            self.assertEqual(pr_ops._landed(".", "m", "b"), "")
        with mock.patch.object(pr_base, "_view", return_value="OPEN"), mock.patch.object(pr_base, "_git", side_effect=lambda r, *a, **k: R(1)):
            self.assertIn("has work that is not in m", pr_ops._landed(".", "m", "b"))

        def same(r, *a, **k):
            return R(0) if a[0] in ("rev-parse", "diff") else R(1)

        with mock.patch.object(pr_base, "_git", side_effect=same):
            self.assertEqual(pr_ops._landed(".", "m", "b", pr=False), "")

        def cherry(r, *a, **k):
            return R(1) if a[0] == "diff" else (R(0, "- abc\n") if a[0] == "cherry" else R(0))

        with mock.patch.object(pr_base, "_git", side_effect=cherry):
            self.assertEqual(pr_ops._landed(".", "m", "b", pr=False), "")

        def cherry_plus(r, *a, **k):
            return R(1) if a[0] == "diff" else (R(0, "+ abc\n") if a[0] == "cherry" else R(0))

        with mock.patch.object(pr_base, "_git", side_effect=cherry_plus):
            self.assertNotEqual(pr_ops._landed(".", "m", "b", pr=False), "")

    def test_ready_outcomes(self):
        cfg = {"pr_checks_timeout": 5}
        head = {"gh pr view b --json headRefOid": R(0, '{"headRefOid": "sha"}')}
        green = json.dumps([{"name": "ci", "status": "completed", "conclusion": "success"}])
        ok = {"gh pr ready": R(0), "gh run list": R(0, green), **head}
        with mock.patch.object(pr_base, "_run", fake(ok)):
            self.assertEqual(pr_ops._ready(".", cfg, "b"), "marked ready; checks green")
        with (
            mock.patch.object(pr_base, "_run", fake({**ok, "gh pr ready": R(1, "", "x")})),
            mock.patch.object(pr_base, "_view", side_effect=lambda r, b, f: "sha" if f == "headRefOid" else False),
        ):
            self.assertEqual(pr_ops._ready(".", cfg, "b"), "already ready; checks green")
        with (
            mock.patch.object(pr_base, "_run", fake({**ok, "gh pr ready": R(1, "", "cannot")})),
            mock.patch.object(pr_base, "_view", return_value=True),
            self.assertRaisesRegex(pr_ops.Blocked, "still a draft: cannot"),
        ):
            pr_ops._ready(".", cfg, "b")
        with mock.patch.object(pr_base, "_run", fake({**ok, "gh run list": R(0, "[]")})):
            self.assertEqual(pr_ops._ready(".", cfg, "b"), "marked ready; no checks configured")
        red = json.dumps([{"name": "lint", "status": "completed", "conclusion": "failure"}])
        with (
            mock.patch.object(pr_base, "_run", fake({**ok, "gh run list": R(0, red)})),
            self.assertRaisesRegex(pr_ops.Blocked, "CI red on b: lint"),
        ):
            pr_ops._ready(".", cfg, "b")

        # the workflow runs stay pending: the clock jumps 3 s per read, so the 5 s limit passes without a real sleep
        pending = json.dumps([{"name": "ci", "status": "in_progress", "conclusion": ""}])
        clock = SimpleNamespace(monotonic=itertools.count(0, 3).__next__, sleep=lambda s: None)
        with (
            mock.patch.object(pr_ops, "time", clock),
            mock.patch.object(pr_base, "_run", fake({**head, "gh run list": R(0, pending)})),
            self.assertRaisesRegex(pr_ops.Blocked, "not finished after 5s"),
        ):
            pr_ops._ready(".", cfg, "b")

    def test_main_prints_verdict(self):
        out = io.StringIO()
        with mock.patch.object(pr_steps, "run", return_value=("DONE", "ok")), contextlib.redirect_stdout(out):
            self.assertEqual(pr_steps.main(["open", "--stage", "s", "--name", "n"]), 0)
        self.assertEqual(out.getvalue(), "VERDICT: DONE\nok\n")


class PrstepsUnits(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.cfg({})
        self.a = SimpleNamespace(dir=self.d, stage="s")

    def cfg(self, c):
        with open(f"{self.d}/pipeline.json", "w") as f:
            json.dump(c, f)

    def test_run_swallows_missing_binary_and_timeout(self):
        with mock.patch.object(pr_mode.subprocess, "run", side_effect=FileNotFoundError), mock.patch.object(pr_mode.gitx, "run", side_effect=FileNotFoundError):
            self.assertIsNone(pr_mode._run(["gh"], self.d))
            self.assertFalse(pr_mode.is_git(self.a))
            self.assertFalse(pr_mode.has_origin(self.a))
        with mock.patch.object(pr_mode.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(pr_mode._run(["gh"], self.d))
        with mock.patch.object(pr_mode.gitx, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertFalse(pr_mode.is_git(self.a))

    def test_active_flags(self):
        with mock.patch.object(pr_mode, "_run", return_value=R(0)), mock.patch.object(pr_mode, "_git", return_value=R(0)):
            self.assertTrue(pr_mode.pr_active(self.a))
            self.assertTrue(pr_mode.sweep_active(self.a))
            self.assertTrue(pr_mode.cleanup_active(self.a))
            self.assertTrue(pr_mode.local_merge_active(self.a))
            self.assertFalse(pr_mode.pr_active(SimpleNamespace(dir=self.d, stage="s", dry_run=True)))
            self.assertFalse(pr_mode.cleanup_active(SimpleNamespace(dir=self.d, stage="s", dry_run=True)))
            self.cfg({"pr": False, "deps_sweep": False})
            self.assertFalse(pr_mode.pr_active(self.a))
            self.assertFalse(pr_mode.sweep_active(self.a))
            self.cfg({"branch_work": "dev"})
            self.assertFalse(pr_mode.pr_active(self.a))
            self.assertFalse(pr_mode.cleanup_active(self.a))
        with mock.patch.object(pr_mode, "_run", side_effect=lambda args, cwd: R(1) if args[0] == "gh" else R(0)):
            self.cfg({})
            self.assertFalse(pr_mode.pr_active(self.a))
            self.assertFalse(pr_mode.sweep_active(self.a))

    def test_sweep_before_branch(self):
        with mock.patch.object(pr_mode, "sweep_active", return_value=False):
            self.assertEqual(pr_ops.sweep_before_branch(self.a), "")
        out = io.StringIO()
        with (
            mock.patch.object(pr_mode, "sweep_active", return_value=True),
            mock.patch.object(pr_ops, "run", return_value=("DONE", "swept 2")) as run,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(pr_ops.sweep_before_branch(self.a), "")
        run.assert_called_once_with("sweep", self.d, "s", "s")
        self.assertIn("== deps-sweep DONE\nswept 2", out.getvalue())

    def test_cmd_sweep_cmd_branch(self):
        self.assertIn(f"pr_steps.py push --dir {os.path.abspath(self.d)} --stage s --name feat", pr_mode.cmd(self.a, "feat", "push"))
        self.assertIn("pr_steps.py sweep", pr_mode.sweep_cmd(SimpleNamespace(dir=self.d, stage="s", feature="f1")))
        self.assertIn("--name f1", pr_mode.sweep_cmd(SimpleNamespace(dir=self.d, stage="s", feature="f1")))
        self.assertTrue(pr_mode.branch(self.a, "feat").endswith("feat") or "feat" in pr_mode.branch(self.a, "feat"))


if __name__ == "__main__":
    unittest.main()
