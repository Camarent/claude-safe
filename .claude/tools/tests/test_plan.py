import argparse
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brieflint
from pipeline import claude, cli_plan
from pipeline import plan_files as pf
from pipeline.configuration import tool_cmd
from pipeline.tasks import parse_flags, parse_tasks
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp


def res(text, cost=0.1):
    return {"result": text, "num_turns": 2, "duration_ms": 1000, "total_cost_usd": cost, "is_error": False, "usage": {}}


class PlanTest(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        os.environ["PLAN_NO_CONTEXT"] = "1"  # tests must not call the real lp (slow, environment-dependent)
        self.d = tracked_mkdtemp()
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True)  # plan_check.check_git_paths wants Predicted paths inside a git repo
        for f in ("a/x.js", "src/core/a.js"):  # anchors in the sample tasks must point at real files (plan_check.check_refs)
            os.makedirs(os.path.dirname(os.path.join(self.d, f)), exist_ok=True)
            Path(os.path.join(self.d, f)).write_text("\n" * 20, encoding="utf-8")
        Path(os.path.join(self.d, ".gitignore")).write_text(".work/\np.txt\npipeline.json\n", encoding="utf-8")  # files the tests write later
        for a in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):  # clean tree: plan.py run refuses a dirty one
            subprocess.run(["git", "-C", self.d, *a], check=True, capture_output=True)
        self.calls, self.handler = [], lambda args: res("VERDICT: DONE")
        self._c = claude.run_claude
        claude.run_claude = self.fake  # type: ignore[assignment]  # test double
        self.prompt = os.path.join(self.d, "p.txt")

    def tearDown(self):
        claude.run_claude = self._c

    def fake(self, args, cwd, timeout):
        self.calls.append(args)
        return self.handler(args)

    def p(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_plan.main([*argv, "--dir", self.d])
        return rc, out.getvalue(), err.getvalue()

    def flags_of(self, stage):
        txt = Path(os.path.join(self.d, ".work", stage, "TASKS.md")).read_text(encoding="utf-8")
        return next(line for line in txt.splitlines() if line.startswith("flags:"))

    def test_only_the_last_phase_keeps_the_publish_tail(self):
        from pipeline.tasks import parse_flags

        self.p("plan-init", "--feature", "F", "--goal", "g")
        self.p("phase", "--feature", "F", "--name", "core", "--goal", "c", "--done-when", "true", "--paths", "src/core")
        self.p("stage-init", "--stage", "F/phases/01-core", "--goal", "g", "--flags", "docs")

        def tm(ph):
            return os.path.join(self.d, ".work", "F", "phases", ph, "TASKS.md")

        self.assertTrue(parse_flags(tm("01-core"))["push"])  # alone it IS the last phase: full tail
        self.p("phase", "--feature", "F", "--name", "ui", "--goal", "u", "--done-when", "true", "--paths", "src/ui", "--depends", "core")
        self.p("stage-init", "--stage", "F/phases/02-ui", "--goal", "g", "--flags", "docs")
        mid, last = parse_flags(tm("01-core")), parse_flags(tm("02-ui"))  # phases added later still count: decided at run time
        self.assertEqual([mid[k] for k in ("push", "index", "live", "clean")], [False] * 4)
        self.assertTrue(mid["audit"] and mid["merge"] and mid["test"])  # audits: by size (phase_audit); tasks still merge into the work branch
        self.assertEqual([last[k] for k in ("push", "index", "live", "clean")], [True] * 4)
        self.p("stage-init", "--stage", "plain", "--goal", "g", "--flags", "docs")
        self.assertTrue(parse_flags(os.path.join(self.d, ".work", "plain", "TASKS.md"))["push"])  # an ordinary stage is untouched

    def task(self, n, paths, **kw):
        extra = [x for k, v in kw.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        return self.p(
            "task",
            "--stage",
            "s",
            "--id",
            str(n),
            "--title",
            f"Do {n}",
            "--paths",
            paths,
            "--test",
            "node --test t.js",
            "--done",
            "tests green",
            "--pattern",
            "a/x.js:10",
            *extra,
        )

    def stage(self):
        Path(self.d, "CLAUDE.md").write_text("## Docs\ndocs/\n", encoding="utf-8")  # the docs flag needs a docs target (plan check)
        self.assertEqual(self.p("stage-init", "--stage", "s", "--goal", "g", "--flags", "ui docs")[0], 0)

    def test_stage_init_writes_the_pr_title_line(self):
        subprocess.run(["git", "init", "-q", self.d], check=True)
        subprocess.run(["git", "-C", self.d, "remote", "add", "origin", "https://example.invalid/x.git"], check=True)
        rc = self.p("stage-init", "--stage", "s", "--goal", "g", "--flags", "docs", "--pr-title", "docs(readme): add demo")[0]
        self.assertEqual(rc, 0)
        text = Path(f"{self.d}/.work/s/TASKS.md").read_text(encoding="utf-8")
        self.assertIn("Goal: g\npr-title: docs(readme): add demo\nflags: docs\n", text)
        self.assertEqual(self.task(1, "a/x.js")[0], 0)

    def test_stage_init_drops_pr_title_without_origin(self):
        subprocess.run(["git", "init", "-q", self.d], check=True)
        self.assertEqual(self.p("stage-init", "--stage", "s", "--goal", "g", "--flags", "docs", "--pr-title", "docs(readme): add demo")[0], 0)
        self.assertNotIn("pr-title", Path(f"{self.d}/.work/s/TASKS.md").read_text(encoding="utf-8"))

    def test_stage_and_tasks_parse(self):
        self.stage()
        self.assertEqual(self.task(1, "a/x.js", affected="none")[0], 0)
        self.assertEqual(self.task(2, "b/y.js", model="sonnet", affected="none")[0], 0)
        self.assertEqual(self.task(3, "a/x.js", depends="1,2", affected="none")[0], 0)
        t = f"{self.d}/.work/s/TASKS.md"
        got = parse_tasks(t)
        self.assertEqual([(x["id"], x["wave"], x["owner_sonnet"]) for x in got], [("task-1", 1, False), ("task-2", 1, False), ("task-3", 2, False)])
        self.assertTrue(parse_flags(t)["ui"] and parse_flags(t)["docs"])
        self.assertEqual(self.p("check", "--stage", "s")[1].strip(), "OK 3 tasks")

    def test_generated_task_passes_brieflint(self):
        self.stage()
        self.task(1, "a/x.js", do_not_touch="c/", turns=30)
        txt = Path(f"{self.d}/.work/s/task-1.md").read_text(encoding="utf-8")
        self.assertEqual(brieflint.lint(txt), [])
        self.assertIn("turns <= 30", txt)
        self.assertIn("qrun.py -- node --test t.js", txt)

    def test_same_wave_overlap_rejected(self):
        self.stage()
        self.task(1, "a/", wave=1)
        rc, _, err = self.task(2, "a/x.js", wave=1)
        self.assertEqual(rc, 2)
        self.assertIn("share paths", err)
        self.assertFalse(os.path.exists(f"{self.d}/.work/s/task-2.md"))
        self.assertEqual(self.task(2, "a/x.js", wave=2)[0], 0)

    def test_unknown_dep_and_rewrite(self):
        self.stage()
        self.assertEqual(self.task(1, "a/x.js", depends="9")[0], 2)
        self.task(1, "a/x.js")
        self.task(1, "b/z.js")
        self.assertEqual(len(parse_tasks(f"{self.d}/.work/s/TASKS.md")), 1)

    def test_edit_task(self):
        self.stage()
        self.task(1, "a/x.js", affected="none")
        self.task(2, "b/y.js", affected="none")
        rc = self.p(
            "edit-task",
            "--stage",
            "s",
            "--id",
            "1",
            "--field",
            "paths",
            "--value",
            "c/z.js",
            "--field",
            "title",
            "--value",
            "New",
            "--field",
            "test",
            "--value",
            "true",
        )[0]
        self.assertEqual(rc, 0)
        txt = Path(f"{self.d}/.work/s/task-1.md").read_text(encoding="utf-8")
        self.assertIn("Predicted paths: c/z.js", txt)
        self.assertIn("Test: " + tool_cmd("qrun.py") + " -- true", txt)
        self.assertIn("# task-1: New", txt)
        self.assertEqual(parse_tasks(f"{self.d}/.work/s/TASKS.md")[0]["title"], "New") if "title" in parse_tasks(f"{self.d}/.work/s/TASKS.md")[
            0
        ] else self.assertIn("task-1 New", Path(f"{self.d}/.work/s/TASKS.md").read_text(encoding="utf-8"))
        self.assertEqual(self.p("edit-task", "--stage", "s", "--id", "1", "--field", "bogus", "--value", "x")[0], 2)
        self.assertEqual(self.p("edit-task", "--stage", "s", "--id", "9", "--field", "done", "--value", "x")[0], 2)
        self.assertEqual(self.p("edit-task", "--stage", "s", "--id", "1", "--field", "paths", "--value", "b/y.js")[0], 2)
        self.assertEqual(self.p("check", "--stage", "s")[1].strip(), "OK 2 tasks")

    def test_check_failures(self):
        self.stage()
        self.task(1, "a/x.js")
        self.task(2, "b/y.js", depends=1)
        os.remove(f"{self.d}/.work/s/task-2.md")
        with open(f"{self.d}/.work/s/task-1.md", "w") as f:
            f.write("nothing\n")
        rc, out, _ = self.p("check", "--stage", "s")
        self.assertEqual(rc, 2)
        self.assertIn("task-2: missing", out)
        self.assertIn("task-1: no qrun.py", out)
        # cycle / bad order: hand-edit task-1 to depend on task-2 in the same wave
        t = f"{self.d}/.work/s/TASKS.md"
        s = Path(t).read_text(encoding="utf-8").replace("review: wave W1\n", "review: wave W1 - depends: task-2\n", 1)
        Path(t).write_text(s, encoding="utf-8")
        self.assertIn("cycle or bad order", self.p("check", "--stage", "s")[1])

    def test_check_overlap_in_wave(self):
        self.stage()
        self.task(1, "a/x.js")
        self.task(2, "b/y.js")
        t2 = f"{self.d}/.work/s/task-2.md"
        txt = Path(t2).read_text(encoding="utf-8").replace("Predicted paths: b/y.js", "Predicted paths: a/x.js")
        Path(t2).write_text(txt, encoding="utf-8")
        self.assertIn("share paths", self.p("check", "--stage", "s")[1])

    def fill_checklist(self, feature="f"):
        path = f"{self.d}/.work/{feature}/Plan.md"
        txt = Path(path).read_text(encoding="utf-8")
        Path(path).write_text(txt.replace("## Acceptance checklist\n", "## Acceptance checklist\n- cmd: true\n"), encoding="utf-8")

    def feature(self):
        self.assertEqual(self.p("plan-init", "--feature", "f", "--goal", "big goal")[0], 0)
        self.fill_checklist()
        self.assertEqual(self.p("phase", "--feature", "f", "--name", "core", "--goal", "g", "--done-when", "node --test", "--paths", "src/core")[0], 0)
        self.assertEqual(
            self.p("phase", "--feature", "f", "--name", "ui", "--goal", "g2", "--done-when", "true", "--depends", "core", "--paths", "src/ui")[0], 0
        )

    def test_empty_checklist_fails_check(self):
        self.p("plan-init", "--feature", "e", "--goal", "g")
        rc, out, _ = self.p("check", "--feature", "e")
        self.assertEqual(rc, 2)
        self.assertIn("Acceptance checklist", out)
        self.fill_checklist("e")
        self.assertEqual(self.p("check", "--feature", "e")[1].strip(), "OK 0 tasks")

    def test_phase_insert_renumbers_unstarted_phases(self):
        self.feature()  # 01-core, 02-ui (depends core)
        self.assertEqual(self.p("phase", "--feature", "f", "--name", "docs", "--goal", "d", "--done-when", "true", "--paths", "docs", "--after", "core")[0], 0)
        names = [x["dir"] for x in pf.read_phases(argparse.Namespace(dir=self.d, feature="f"))]
        self.assertEqual(names, ["01-core", "02-docs", "03-ui"])
        self.assertTrue(os.path.isdir(f"{self.d}/.work/f/phases/03-ui") and not os.path.exists(f"{self.d}/.work/f/phases/02-ui"))
        ui = pf.read_phases(argparse.Namespace(dir=self.d, feature="f"))[2]
        self.assertEqual(ui["depends"], ["core"])  # suffix name stays valid
        Path(f"{self.d}/.work/f/phases/03-ui/TASKS.md").write_text("x", encoding="utf-8")
        rc, _, err = self.p("phase", "--feature", "f", "--name", "late", "--goal", "g", "--done-when", "true", "--paths", "late", "--after", "core")
        self.assertEqual(rc, 2)
        self.assertIn("has started", err)

    def test_phases(self):
        self.feature()
        self.assertTrue(os.path.isdir(f"{self.d}/.work/f/phases/01-core") and os.path.isdir(f"{self.d}/.work/f/phases/02-ui"))
        rc, _, err = self.p("phase", "--feature", "f", "--name", "bad", "--goal", "g", "--done-when", "x", "--paths", "src/core/a.js")
        self.assertEqual(rc, 2)
        self.assertIn("overlap", err)
        self.assertIn("01-core", err)
        self.assertEqual(self.p("check", "--feature", "f")[1].strip(), "OK 0 tasks")
        nxt = json.loads(self.p("next-phase", "--feature", "f")[1])
        self.assertEqual(nxt["dir"], "01-core")
        self.p("phase-done", "--feature", "f", "--name", "core")
        self.assertEqual(json.loads(self.p("next-phase", "--feature", "f")[1])["dir"], "02-ui")
        self.p("phase-done", "--feature", "f", "--name", "ui")
        self.assertEqual(json.loads(self.p("next-phase", "--feature", "f")[1]), {"phase": None, "all_done": True})

    def test_check_feature_problems(self):
        self.feature()
        os.rmdir(f"{self.d}/.work/f/phases/02-ui")
        rc, out, _ = self.p("check", "--feature", "f")
        self.assertEqual(rc, 2)
        self.assertIn("02-ui: folder", out)
        os.makedirs(f"{self.d}/.work/f/phases/02-ui")
        pl = f"{self.d}/.work/f/Plan.md"
        s = Path(pl).read_text(encoding="utf-8").replace("| true |", "|  |").replace("src/ui", "src/core/x")
        Path(pl).write_text(s, encoding="utf-8")
        out = self.p("check", "--feature", "f")[1]
        self.assertIn("no done_when", out)
        self.assertIn("share paths", out)

    def sized(self, *phases):
        self.p("plan-init", "--feature", "f", "--goal", "g")
        self.fill_checklist()
        for i, (goal, n) in enumerate(phases):
            self.p("phase", "--feature", "f", "--name", f"p{i}", "--goal", goal, "--done-when", "true", "--paths", f"src/p{i}", "--tasks", str(n))
        return self.p("check", "--feature", "f")

    def test_bump_phase_rejected_when_release_script_is_set(self):
        Path(f"{self.d}/pipeline.json").write_text(json.dumps({"release_script": "scripts/bump-version.sh"}), encoding="utf-8")
        self.addCleanup(os.remove, f"{self.d}/pipeline.json")
        _rc, out, _ = self.sized(("gate: owner step or real-data run - Bump to v0.3.0 and CHANGELOG", 5), ("g2", 20))
        self.assertIn("01-p0: plans a version bump", out)
        _, out, _ = self.sized(("gate: owner step or real-data run - handle the version field parser", 5), ("g2", 20))
        self.assertNotIn("plans a version bump", out)

    def test_size_one_phase_is_a_stage(self):
        rc, out, _ = self.sized(("only", 20))
        self.assertEqual(rc, 2)
        self.assertIn("use stage-init", out)

    def test_size_small_phase_needs_gate(self):
        rc, out, _ = self.sized(("gate: owner step or real-data run - x", 5), ("g2", 20))
        self.assertEqual((rc, "estimate" in out), (0, False))
        rc, out, _ = self.sized(("gate: a later phase follows", 5), ("g2", 20))
        self.assertEqual(rc, 2)
        self.assertIn("01-p0: estimate 5", out)
        self.assertEqual(self.sized(("g1", 0), ("g2", 3))[0], 2)  # unknown estimate is skipped, 3 is flagged

    def test_size_combine_is_a_warning_only(self):
        rc, out, _ = self.sized(("g1", 12), ("g2", 12))
        self.assertEqual(rc, 0)
        self.assertIn("combine: 01-p0 and 02-p1", out)
        self.assertNotIn("combine:", self.sized(("g1", 15), ("g2", 15))[1])

    def test_plan_md_without_estimate_column_still_parses(self):
        self.feature()
        pl = f"{self.d}/.work/f/Plan.md"
        s = Path(pl).read_text(encoding="utf-8")
        s = "\n".join(line.rsplit("|", 2)[0] + "|" if line.startswith("| 0") else line for line in s.splitlines())
        Path(pl).write_text(s, encoding="utf-8")
        self.assertEqual([p["tasks"] for p in cli_plan.pf.read_phases(cli_plan.argparse.Namespace(dir=self.d, feature="f"))], [0, 0])

    def test_phase_stage_resolves_and_tasks(self):
        self.feature()
        self.assertEqual(self.p("stage-init", "--stage", "01-core", "--goal", "g")[0], 0)
        self.assertTrue(os.path.exists(f"{self.d}/.work/f/phases/01-core/TASKS.md"))
        self.p(
            "task",
            "--stage",
            "01-core",
            "--id",
            "1",
            "--title",
            "t",
            "--paths",
            "src/core/a.js",
            "--test",
            "true",
            "--done",
            "ok",
            "--pattern",
            "src/core/a.js:3",
            "--affected",
            "none",
        )
        self.assertEqual(self.p("check", "--feature", "f")[1].strip(), "OK 1 tasks")

    # ---- run ----
    def test_run_dry(self):
        Path(self.prompt).write_text("add a button", encoding="utf-8")
        rc, out, _ = self.p("run", "--feature", "s", "--prompt-file", self.prompt, "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("model: sonnet", out)
        self.assertIn("plan.py stage-init", out)
        self.assertIn("plan.py plan-init", out)  # one prompt offers both routes
        self.assertEqual(len(self.calls), 0)  # a dry run is free

    def run_flow(self, big_agent):
        """Architect fake: the n-th architect call runs big_agent(n)."""
        Path(self.prompt).write_text("add a button", encoding="utf-8")
        seq = {"n": 0}

        def h(args):
            if "--agent" not in args:
                return res("unexpected non-architect call")
            seq["n"] += 1
            return res(big_agent(seq["n"], args))

        self.handler = h
        return self.p("run", "--feature", "s", "--prompt-file", self.prompt), seq

    def write_plan(self, ok=True):
        self.stage()
        self.task(1, "a/x.js", affected="none")
        if not ok:
            os.remove(f"{self.d}/.work/s/task-1.md")

    def test_run_ok(self):
        (rc, out, _), seq = self.run_flow(lambda n, a: (self.write_plan(), "VERDICT: DONE")[1])
        self.assertEqual((rc, seq["n"]), (0, 1))
        self.assertIn("OK 1 tasks", out)

    def test_run_escalate_reruns_on_opus(self):
        models = []

        def a(n, args):
            models.append(args[args.index("--model") + 1])
            if n == 1:
                return "ESCALATE: hard"
            self.write_plan()
            return "VERDICT: DONE"

        (rc, _, _), _seq = self.run_flow(a)
        self.assertEqual((rc, models), (0, ["sonnet", "opus"]))

    def test_run_escalate_handoff_reaches_opus(self):
        prompts = []

        def a(n, args):
            prompts.append(args[-1])
            if n == 1:
                os.makedirs(f"{self.d}/.work/s", exist_ok=True)
                Path(f"{self.d}/.work/s/ESCALATE.md").write_text("why: contract", encoding="utf-8")
                return "ESCALATE: contract"
            self.write_plan()
            return "VERDICT: DONE"

        self.run_flow(a)
        self.assertIn("write .work/s/ESCALATE.md", prompts[0])
        self.assertIn("read .work/s/ESCALATE.md first", prompts[1])
        self.assertNotIn("read .work/s/ESCALATE.md first", prompts[0])

    def test_run_invalid_plan_rerun(self):
        prompts = []

        def a(n, args):
            prompts.append(args[-1])
            self.write_plan(ok=n > 1)
            return "VERDICT: DONE"

        (rc, _out, _), seq = self.run_flow(a)
        self.assertEqual((rc, seq["n"]), (0, 2))
        self.assertIn("task-1: missing", prompts[1])

    def test_run_still_invalid_exit2(self):
        (rc, out, _), seq = self.run_flow(lambda n, a: (self.write_plan(ok=False), "VERDICT: DONE")[1])
        self.assertEqual((rc, seq["n"]), (2, 2))
        self.assertIn("task-1: missing", out)

    def test_run_blocked(self):
        (rc, out, _), seq = self.run_flow(lambda n, a: "VERDICT: BLOCKED")
        self.assertEqual((rc, seq["n"]), (2, 1))
        self.assertIn("BLOCKED", out)


class CheckRefsTest(unittest.TestCase):
    def test_stale_anchor_and_new_file(self):
        from pipeline import plan_check

        d = tracked_mkdtemp()
        os.makedirs(os.path.join(d, "repo", "src"))
        os.makedirs(os.path.join(d, ".work", "S"))
        Path(os.path.join(d, "repo", "src", "a.py")).write_text("x\ny\n", encoding="utf-8")
        with open(os.path.join(d, ".work", "S", "task-1.md"), "w", encoding="utf-8") as f:
            f.write("Predicted paths: src/new.py\nPattern: src/new.py:1\nsee src/a.py:2, src/a.py:9, gone/x.py:3\n")
        p = plan_check.check_refs(os.path.join(d, ".work", "S"), "task-1", os.path.join(d, "repo"))
        self.assertEqual(len(p), 2)
        self.assertTrue(any("src/a.py:9" in m and "only 2 lines" in m for m in p))
        self.assertTrue(any("gone/x.py:3" in m and "not found" in m for m in p))


class CheckTestGlobsTest(unittest.TestCase):
    def _run(self, paths, test_line, files=()):
        from pipeline import plan_check

        d = tracked_mkdtemp()
        os.makedirs(os.path.join(d, "repo", "tests"))
        os.makedirs(os.path.join(d, ".work", "S"))
        for f in files:
            Path(os.path.join(d, "repo", f)).write_text("x\n", encoding="utf-8")
        with open(os.path.join(d, ".work", "S", "task-1.md"), "w", encoding="utf-8") as fh:
            fh.write(f"Predicted paths: {paths}\nTest: {test_line}\n")
        return plan_check.check_test_globs(os.path.join(d, ".work", "S"), "task-1", os.path.join(d, "repo"))

    def test_empty_glob_fails(self):
        p = self._run("a.js", "python3 -I /root/.claude/tools/qrun.py -- node --test tests/*.test.mjs", ["tests/a.test.js"])
        self.assertEqual(len(p), 1)
        self.assertIn("tests/*.test.mjs", p[0])

    def test_matching_glob_passes(self):
        self.assertEqual(self._run("a.js", "qrun.py -- node --test tests/*.test.js", ["tests/a.test.js"]), [])

    def test_predicted_path_creates_the_test(self):
        self.assertEqual(self._run("tests/new.test.js", "qrun.py -- node --test tests/new.test.js"), [])

    def test_tool_paths_options_and_dirs_ignored(self):
        self.assertEqual(self._run("a.js", "/root/.claude/tools/qrun.py -- cargo test --release tests/"), [])


if __name__ == "__main__":
    unittest.main()
