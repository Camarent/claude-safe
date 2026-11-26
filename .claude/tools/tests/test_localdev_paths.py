"""Characterization tests for localdev: pack merge/cap/judge paths, read-only failures, code_task guards, run_agent command line. No model, no network."""

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LOCALDEV_HOME"] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "localmodel", "data")
from localmodel import localdev
from search import ask_code, strata
from search import strata as strata_mod
from tests.support.tmpdirs import tracked_mkdtemp


def git(cwd, *a):
    return subprocess.run(
        ["git", *a],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
        check=False,
    )


class Repo(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("search.strata.strata_up", return_value=True)  # the local model is faked in these tests
        patcher.start()
        self.addCleanup(patcher.stop)
        self.d = tracked_mkdtemp()
        os.makedirs(f"{self.d}/pkg")
        os.makedirs(f"{self.d}/.work/s")
        Path(f"{self.d}/pkg/a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        Path(f"{self.d}/pkg/b.py").write_text("".join(f"line{i} word{i}\n" for i in range(1, 41)), encoding="utf-8")
        Path(f"{self.d}/test_a.py").write_text(
            "import unittest\nfrom pkg.a import f\nclass T(unittest.TestCase):\n    def test(self): self.assertEqual(f(), 2)\n", encoding="utf-8"
        )
        Path(f"{self.d}/.gitignore").write_text(".work/localdev/\n", encoding="utf-8")
        Path(f"{self.d}/.work/s/task-1.md").write_text("make f return 2\n", encoding="utf-8")
        git(self.d, "init", "-q")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "base")
        self.old = os.getcwd()
        os.chdir(self.d)
        self.addCleanup(os.chdir, self.old)
        self.addCleanup(lambda: os.environ.pop("LOCALDEV_CMD", None))

    def run_main(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = localdev.main(list(args))
        return rc, out.getvalue()


class PackPaths(Repo):
    def pack(self, strata, search, rg=None, goal="Where is `applyIt` used and what does it do for the cart", extra=()):
        real = subprocess.run

        def run(args, *a, **k):
            if args[0] == "rg":
                return subprocess.CompletedProcess(args, 0, rg or "", "")
            return real(args, *a, **k)

        with (
            mock.patch.object(ask_code, "refresh"),
            mock.patch.object(strata_mod, "strata_call", strata),
            mock.patch.object(ask_code, "query", search),
            mock.patch.object(subprocess, "run", run),
        ):
            return self.run_main("pack", "--out", ".work/s/pack.md", "--goal", goal, *extra)

    def test_merge_touching_windows_exact_first_and_cap(self):
        def chunk(f, s, e):
            lines = Path(f).read_text(encoding="utf-8").splitlines()
            return {"file": f, "start": s, "end": e, "text": "\n".join(lines[s - 1 : e])}

        def search(root, q, k=2):
            return [chunk("pkg/b.py", 1, 10), chunk("pkg/b.py", 11, 20), chunk("pkg/a.py", 1, 2)]

        def strata(p, max_tokens=1500):
            if "List up to 8" in p:
                return '{"queries": ["first thing in the cart", 7]}'
            return '{"summary": "S", "why": {"0": "zero", "1": "one", "x": "bad", "9": "far"}}'

        rg = "pkg/b.py:3:def applyIt(x):\n"
        rc, out = self.pack(strata, search, rg=rg)
        self.assertEqual(rc, 0, out)
        pack = Path(".work/s/pack.md").read_text(encoding="utf-8")
        self.assertIn("queries=2", out)
        self.assertIn("excerpts=2/2", out)
        # rg hit (1-15) absorbs the touching 1-10 / 11-20 windows; the exact hit keeps first place
        self.assertIn("## Where to look\n- pkg/b.py:1-15 - one\n- pkg/a.py:1-2 - zero\n", pack)

    def test_word_cap_drops_non_exact_excerpts(self):
        big = {"file": "pkg/b.py", "start": 1, "end": 40, "text": "\n".join(f"w{i} x{i}" for i in range(40))}
        small = {"file": "pkg/a.py", "start": 1, "end": 2, "text": "def f():\n    return 1"}
        rc, out = self.pack(lambda p, max_tokens=1500: "no json", lambda *a, **k: [big, small], extra=("--max-words", "20"))
        self.assertEqual(rc, 0, out)
        self.assertIn("excerpts=1/2", out)

    def test_judge_garbage_falls_back_to_goal_text(self):
        def strata(p, max_tokens=1500):
            return '{"queries": ["x"]}' if "List up to 8" in p else "{"

        rc, out = self.pack(strata, lambda *a, **k: [{"file": "pkg/a.py", "start": 1, "end": 2, "text": "def f():\n    return 1"}])
        self.assertEqual(rc, 0, out)
        self.assertIn("## Goal\nWhere is `applyIt`", Path(".work/s/pack.md").read_text(encoding="utf-8"))

    def test_identifier_hits_rows(self):
        rg = "pkg/b.py:5:use applyIt here\npkg/b.py:2:def applyIt(): pass\nnoise\nmissing.py:1:applyIt\n"
        with mock.patch.object(subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 0, rg, "")):
            hits = localdev.identifier_hits(self.d, "call `applyIt` now")
        self.assertEqual([(h["file"], h["start"], h["end"]) for h in hits], [("pkg/b.py", 1, 14), ("pkg/b.py", 3, 17)])
        self.assertTrue(all(h["exact"] == "applyIt" for h in hits))

    def test_identifier_hits_rg_missing(self):
        def boom(*a, **k):
            raise OSError("no rg")

        with mock.patch.object(subprocess, "run", boom):
            self.assertEqual(localdev.identifier_hits(self.d, "call `applyIt` now"), [])


class ReadOnlyPaths(Repo):
    def test_failure_line_reports_stop_and_rc(self):
        os.environ["LOCALDEV_CMD"] = "echo oops; exit 3"
        rc, out = self.run_main("bulk", "--out", ".work/s/o.md", "--instruction", "g", "pkg/a.py")
        self.assertEqual(rc, 1)
        self.assertIn("LOCALDEV bulk FAILED", out)
        self.assertIn("stop=None rc=3 reply='oops\\n'", out)

    def test_out_outside_exits(self):
        with self.assertRaises(SystemExit):
            self.run_main("bulk", "--out", "../x.md", "--instruction", "g", "pkg/a.py")

    def test_pack_kind_reports_cites_and_oversize(self):
        os.environ["LOCALDEV_CMD"] = "echo 'see pkg/a.py:1-2 and pkg/a.py:99 and nope.py:3 one two three four five six' > .work/s/p.md"
        a = mock.Mock(out=".work/s/p.md", goal="a goal", max_words=2, max_turns=30, profile=None)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = localdev.read_only_task(a, "pack")
        self.assertEqual(rc, 0)
        self.assertIn("cites=1/3 valid bad=", out.getvalue())
        self.assertIn(" OVERSIZE", out.getvalue())


class CodeGuards(Repo):
    def commit_task(self, text):
        Path(".work/s/task-1.md").write_text(text, encoding="utf-8")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "hdr")

    def test_needs_test_and_allowed(self):
        with self.assertRaises(SystemExit) as c:
            self.run_main("code", "--task", ".work/s/task-1.md", "--id", "g1")
        self.assertIn("need a test command", str(c.exception))

    def test_duplicate_id_fails(self):
        self.commit_task("x\nPredicted paths: pkg/a.py\nTest (loop): `python3 -m unittest -q`\n")
        os.environ["LOCALDEV_CMD"] = "true"
        self.run_main("code", "--task", ".work/s/task-1.md", "--id", "dup")
        with self.assertRaises(SystemExit) as c:
            self.run_main("code", "--task", ".work/s/task-1.md", "--id", "dup")
        self.assertIn("git worktree failed", str(c.exception))

    def test_read_also_must_exist(self):
        self.commit_task("x\nPredicted paths: pkg/a.py\nTest (loop): `python3 -m unittest -q`\nRead also: nowhere\n")
        with self.assertRaises(SystemExit) as c:
            self.run_main("code", "--task", ".work/s/task-1.md", "--id", "r1")
        self.assertIn("Read also", str(c.exception))

    def test_early_exit_leaves_no_worktree(self):  # 'Read also' is checked after the worktree exists: it must be removed again
        self.commit_task("x\nPredicted paths: pkg/a.py\nTest (loop): `python3 -m unittest -q`\nRead also: nowhere\n")
        with self.assertRaises(SystemExit):
            self.run_main("code", "--task", ".work/s/task-1.md", "--id", "r2")
        self.assertFalse(os.path.exists(".work/localdev/r2/wt"))
        self.assertEqual(git(self.d, "branch", "--list", "localdev/r2").stdout.strip(), "")

    def test_timed_out_authoritative_run_is_a_failed_run(self):
        self.commit_task("x\nPredicted paths: pkg/a.py\nTest (loop): `python3 -m unittest test_a -q`\n")
        orig = localdev.sh

        def fake_sh(args, cwd, **kw):
            if args and args[0] == "python3":  # only the authoritative test run times out
                raise subprocess.TimeoutExpired(args, 900)
            return orig(args, cwd, **kw)

        agent = {"turns": 1, "secs": 0.0, "reply": ""}
        with mock.patch.object(localdev, "sh", side_effect=fake_sh), mock.patch.object(localdev, "run_agent", return_value=agent):
            rc, out = self.run_main("code", "--task", ".work/s/task-1.md", "--id", "to1")
        self.assertEqual(rc, 1, out)
        self.assertIn("test=FAIL", out)
        self.assertTrue(os.path.isfile(".work/localdev/to1/report.md"))

    def test_new_file_outside_scope_is_removed(self):
        self.commit_task("x\nPredicted paths: pkg/a.py\nTest (loop): `python3 -m unittest test_a -q`\n")
        os.environ["LOCALDEV_CMD"] = "sed -i 's/return 1/return 2/' pkg/a.py; echo n > newfile.txt"
        rc, out = self.run_main("code", "--task", ".work/s/task-1.md", "--id", "n1")
        self.assertEqual(rc, 0, out)
        self.assertIn("reverted=['newfile.txt']", out)
        self.assertIn("files=['pkg/a.py']", out)
        self.assertFalse(os.path.exists(".work/localdev/n1/wt/newfile.txt"))
        report = Path(".work/localdev/n1/report.md").read_text(encoding="utf-8")
        self.assertTrue(report.startswith("# localdev n1: PASS\nbranch localdev/n1, base "))
        self.assertIn("reverted (outside the task paths): ['newfile.txt']", report)
        self.assertIn("test `python3 -m unittest test_a -q` exit 0", report)

    def test_untracked_task_file_is_not_part_of_the_result(self):
        Path(".work/s/task-2.md").write_text("y\nPredicted paths: pkg/a.py\nTest (loop): `python3 -m unittest test_a -q`\n", encoding="utf-8")
        os.environ["LOCALDEV_CMD"] = "sed -i 's/return 1/return 2/' pkg/a.py"
        rc, out = self.run_main("code", "--task", ".work/s/task-2.md", "--id", "t2")
        self.assertEqual(rc, 0, out)
        self.assertIn("files=['pkg/a.py']", out)


class NulPaths(Repo):
    def test_non_ascii_out_of_scope_file_is_seen_and_reverted(self):  # git quotes "café.txt" unless -z is used
        base = git(self.d, "rev-parse", "HEAD").stdout.strip()
        Path(self.d, "café.txt").write_text("stray\n", encoding="utf-8")
        self.assertIn("café.txt", localdev.changed_vs(base, self.d))
        self.assertIn("café.txt", localdev.changed_files(self.d))
        viol = localdev._revert_out_of_scope(base, self.d, "task.md", ["pkg"])
        self.assertIn("café.txt", viol)
        self.assertFalse(os.path.exists(os.path.join(self.d, "café.txt")))


class Misc(unittest.TestCase):
    def test_ctx_tokens(self):
        for env, want in (("64k", 65536), ("1000", 1000), ("bad", 65536)):
            with mock.patch.dict(os.environ, {"CONTEXT": env}):
                self.assertEqual(localdev.ctx_tokens(), want)

    def test_key_from_env(self):
        with mock.patch.dict(os.environ, {"STRATA_KEY": "k1"}):
            self.assertEqual(strata.strata_key(), "k1")

    def test_cite_check_missing_and_out_of_range(self):
        d = tracked_mkdtemp()
        Path(d, "a.py").write_text("1\n2\n3\n", encoding="utf-8")
        ok, bad, bl = localdev.cite_check("`a.py:2-3` a.py:4 gone.py:1", d)
        self.assertEqual((ok, bad, sorted(bl)), (1, 2, ["a.py:4", "gone.py:1"]))

    def test_changed_files_outside_git(self):
        self.assertIsNone(localdev.changed_files(tracked_mkdtemp()))

    def test_run_agent_command_line(self):
        seen: dict = {}

        def fake_sh(cmd, cwd, **kw):
            seen.update(cmd=cmd, env=kw["env"])
            return subprocess.CompletedProcess(cmd, 0, '{"num_turns": 4, "result": "DONE", "subtype": "success"}', "")

        with mock.patch.dict(os.environ, {"STRATA_KEY": "k1", "ANTHROPIC_API_KEY": "x"}), mock.patch.object(localdev, "sh", fake_sh):
            os.environ.pop("LOCALDEV_CMD", None)
            res = localdev._run_agent("P", "/w", "Read", ["Read"], 7, 10, "SYS", ["/d1"])
        self.assertEqual((res["rc"], res["turns"], res["reply"], res["subtype"]), (0, 4, "DONE", "success"))
        c = seen["cmd"]
        self.assertEqual(c[:3], ["claude", "-p", "P"])
        self.assertEqual(c[c.index("--max-turns") + 1], "7")
        self.assertEqual(c[c.index("--append-system-prompt") + 1], "SYS")
        self.assertEqual(c[c.index("--add-dir") + 1], "/d1")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(seen["env"]["ANTHROPIC_AUTH_TOKEN"], "k1")

    def test_run_agent_timeout_and_plain_output(self):
        def boom(cmd, cwd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch.dict(os.environ, {}), mock.patch.object(localdev, "sh", boom):
            os.environ.pop("LOCALDEV_CMD", None)
            res = localdev._run_agent("P", "/w", "Read", ["Read"], 7, 10, None, ())
        self.assertEqual((res["rc"], res["reply"], res["turns"]), (124, "", None))
        with mock.patch.dict(os.environ, {}), mock.patch.object(localdev, "sh", lambda cmd, cwd, **kw: subprocess.CompletedProcess(cmd, 1, "not json", "")):
            os.environ.pop("LOCALDEV_CMD", None)
            res = localdev._run_agent("P", "/w", "Read", ["Read"], 7, 10, None, ())
        self.assertEqual((res["rc"], res["reply"]), (1, "not json"))

    def test_log_mode(self):
        d = tracked_mkdtemp()
        old = os.getcwd()
        os.chdir(d)
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(localdev.main(["log"]), 0)
            self.assertEqual(out.getvalue(), "0 runs logged\n")
            localdev.log_run(d, {"mode": "pack", "ok": True, "turns": 0, "secs": 1.0, "profiles": ["search"], "out": "o.md"})
            out = io.StringIO()
            with redirect_stdout(out):
                localdev.main(["log", "--last", "1"])
            self.assertIn("pack  OK     turns=0 secs=1.0 profiles=search o.md", out.getvalue())
            self.assertTrue(out.getvalue().endswith("1 runs logged\n"))
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
