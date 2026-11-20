"""audit/gatewatch.py and its use in taskgates: writes outside the worktree are named, a red gate report carries the process environment."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import gatewatch, taskgates
from tests.support.tmpdirs import tracked_mkdtemp


def git_named(table):
    """A fake `git grep -l -z`: TABLE = {name: [files]}. It writes each file (a line naming the pattern) under the repo dir it is called with and answers with the NUL-separated names; other git calls answer with the value of the key `config`."""

    def git(repo, *args):
        if args[0] != "grep":
            return table.get("config")
        pats = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
        names = []
        for p in pats:
            for f in table.get(p, []):
                os.makedirs(os.path.dirname(os.path.join(repo, f)) or repo, exist_ok=True)
                with open(os.path.join(repo, f), "a") as fh:
                    fh.write(f"uses {p}\n")
                names.append(f)
        return "\0".join(dict.fromkeys(names)) + "\0" if names else ""

    return git


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()

    def test_created_removed_changed_are_named(self):
        Path(self.d, "w").mkdir()
        Path(self.d, "w", "keep").write_text("1")
        Path(self.d, "w", "gone").write_text("1")
        a = gatewatch.snapshot(self.d, ["w"])
        Path(self.d, "w", "gone").unlink()
        Path(self.d, "w", "keep").write_text("22")
        Path(self.d, "w", "zztest").mkdir()
        b = gatewatch.snapshot(self.d, ["w"])
        self.assertEqual(gatewatch.changes(a, b), [("w/gone", "removed"), ("w/keep", "changed"), ("w/zztest", "created")])
        self.assertEqual(gatewatch.changes(a, a), [])

    def test_missing_folder_is_empty_and_cut_folder_is_marked(self):
        self.assertEqual(gatewatch.snapshot(self.d, ["nope"]), {})
        Path(self.d, "w").mkdir()
        for i in range(3):
            Path(self.d, "w", f"f{i}").write_text("x")
        with mock.patch.object(gatewatch, "GATE_WATCH_MAX_ENTRIES", 2):
            self.assertIn("...", gatewatch.snapshot(self.d, ["w"]))

    def test_warning_names_the_test_that_mentions_the_path(self):
        before: dict = {}
        after = {"w/zztest": (1, 0)}
        job = tracked_mkdtemp()
        text = gatewatch.warning(job, before, after, git_named({"zztest": ["tests/x.test.js"]}))
        self.assertIn("created: w/zztest (named in tests/x.test.js)", text)
        self.assertEqual(gatewatch.warning(job, after, after, git_named({})), "")

    def test_a_symlink_or_special_file_is_not_read(self):
        job = tracked_mkdtemp()
        outside = os.path.join(tracked_mkdtemp(), "o.txt")
        Path(outside).write_text("zzfile.txt")
        os.symlink(outside, os.path.join(job, "link.js"))
        self.assertEqual(gatewatch._text_of(job, "link.js"), "")
        self.assertEqual(gatewatch._text_of(job, "missing.js"), "")
        Path(job, "real.js").write_text("zzfile.txt")
        self.assertIn("zzfile", gatewatch._text_of(job, "real.js"))

    def test_a_file_name_with_a_colon_stays_whole(self):
        job = tracked_mkdtemp()
        found = gatewatch.offenders(job, ["w/zzfile.txt"], git_named({"zzfile.txt": ["t/a:b.test.js"]}))
        self.assertEqual(found, {"w/zzfile.txt": ["t/a:b.test.js"]})

    def test_two_changed_paths_use_one_grep_call(self):
        calls = []
        inner = git_named({"zza": ["t/a.js"], "zzb": ["t/a.js", "t/b.js"]})

        def git(repo, *args):
            calls.append(args)
            return inner(repo, *args)

        found = gatewatch.offenders(tracked_mkdtemp(), ["w/zza", "w/zzb", "w/none"], git)
        self.assertEqual(found, {"w/zza": ["t/a.js"], "w/zzb": ["t/a.js", "t/b.js"]})
        self.assertEqual(len(calls), 1)

    def test_environment_block_keeps_only_the_named_variables(self):
        env = {"GIT_DIR": "/x", "APP_A": "1", "WT_B": "2", "TZ": "UTC", "HOME": "/h", "PATH": "/p", "SECRET": "no", "TMPDIR": "/t", "OTHER": "3"}
        text = gatewatch.environment(env, "/job", "", extra=("APP_A",))
        for want in ("GIT_DIR=/x", "APP_A=1", "WT_B=2", "TZ=UTC", "HOME=/h", "PATH=/p", "TMPDIR=/t", "cwd: /job", "core.hooksPath: (unset)", "time: "):
            self.assertIn(want, text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("OTHER", text)
        self.assertNotIn("APP_A", gatewatch.environment(env, "/job", ""))  # a project variable only with the project's test_env names

    def test_a_cut_snapshot_is_not_compared(self):
        text = gatewatch.warning("/job", {"...": (0, 0)}, {"w/x": (1, 0)}, git_named({}))
        self.assertIn("was not compared", text)
        self.assertNotIn("created", text)


class InTaskGates(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, "pipeline.json").write_text(json.dumps({"gates": [{"name": "t", "cmd": "mytest"}], "gate_watch": ["w"]}), encoding="utf-8")
        Path(self.d, "w").mkdir()
        self.job = tracked_mkdtemp()
        for name, target in (("workdir", self.job), ("content_sha", "x")):
            p = mock.patch.object(taskgates.testrun if name == "workdir" else taskgates, name, return_value=target)
            p.start()
            self.addCleanup(p.stop)

    def run_it(self, rc):
        def runner(cmd, **kw):
            Path(self.d, "w", "zztest").mkdir(exist_ok=True)
            return SimpleNamespace(returncode=rc if kw["cwd"] == self.job else 0, stdout="out\n", stderr="")  # green on the stage base

        return taskgates.run("s", "task-1", self.d, runner=runner, git=git_named({"zztest": ["tests/a.test.js"], "config": "hooks\n"}))

    def test_no_watch_folders_means_no_warning_and_an_unchanged_gate_stays_quiet(self):
        def quiet(cmd, **kw):
            return SimpleNamespace(returncode=0, stdout="out\n", stderr="")

        _v, text = taskgates.run("s", "task-1", self.d, runner=quiet, git=git_named({}))
        self.assertNotIn("warning", text)
        Path(self.d, "pipeline.json").write_text(json.dumps({"gates": [{"name": "t", "cmd": "mytest"}]}), encoding="utf-8")
        _v, text = self.run_it(0)  # the gate creates w/zztest, but nothing is watched
        self.assertNotIn("warning", text)

    def test_green_run_warns_and_red_run_adds_the_environment(self):
        v, text = self.run_it(0)
        self.assertEqual(v, "DONE")
        self.assertIn("created: w/zztest (named in tests/a.test.js)", text)
        self.assertNotIn("gate process", text)
        Path(self.d, "w", "zztest").rmdir()
        v, text = self.run_it(1)
        self.assertEqual(v, "FAIL")
        self.assertIn("gate process", text)
        self.assertIn("git core.hooksPath: hooks", text)
        self.assertIn("gate t: FAILED", text)


if __name__ == "__main__":
    unittest.main()
