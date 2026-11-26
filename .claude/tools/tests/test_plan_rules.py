"""plan_rules: task size cap, protected gates, untracked/ignored Predicted paths, done_when files, release flag; tasks.parse_tasks keeps an oversized task on Haiku."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules
from pipeline.tasks import parse_tasks

# task ids: asserts use these names, not literals (path-dependent check)
T1, T2 = "task-1", "task-2"


def git(repo, *a):
    subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)


class T(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name

    def task(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def paths(self, n):
        return "Predicted paths: " + ", ".join(f"src/f{i}.py" for i in range(n)) + "\n"

    def test_size_fail_only(self):
        self.task("task-1.md", self.paths(15))
        self.assertEqual(plan_rules.check_task_size(self.d, T1), [])
        self.task("task-1.md", self.paths(16))  # no size warning any more
        self.assertEqual(plan_rules.check_task_size(self.d, T1), [])
        self.task("task-1.md", self.paths(25))  # at the fail cap: still no problem
        self.assertEqual(plan_rules.check_task_size(self.d, T1), [])
        self.task("task-1.md", self.paths(26))
        got = plan_rules.check_task_size(self.d, T1)
        self.assertIn("split by seam", got[0])
        self.assertEqual(plan_check.errors_of(got), got)  # fails the check

    def test_oversized_task_keeps_haiku(self):
        self.task("TASKS.md", "- [ ] task-1 a\n- [ ] task-2 b\n\n## Graph\nwave 1: task-1, task-2\n")
        self.task("task-1.md", self.paths(16))
        self.task("task-2.md", self.paths(3))
        got = {t["id"]: t["owner_sonnet"] for t in parse_tasks(os.path.join(self.d, "TASKS.md"))}
        self.assertEqual(got, {T1: False, T2: False})

    def test_gate_mention_warns_only_when_unnamed(self):
        gates = [("insert-only", ["data/*.json"])]
        self.task("task-1.md", "Predicted paths: data/a.json\nGoal: x\n")
        got = plan_rules.check_gate_mentions(self.d, "task-1", gates)
        self.assertTrue(got and got[0].startswith("gate:"))
        self.assertEqual(plan_check.errors_of(got), [])
        self.task("task-1.md", "Predicted paths: data/a.json\nGoal: x; gate insert-only now allows edits\n")
        self.assertEqual(plan_rules.check_gate_mentions(self.d, T1, gates), [])

    def test_protected_gates_from_config_and_audit(self):
        with open(os.path.join(self.d, "AUDIT.md"), "w", encoding="utf-8") as f:
            f.write("- protected gate ids-frozen: data/ids.json, tests/ids/\n")
        got = plan_rules.protected_gates(self.d, {"protected_gates": [{"name": "g", "paths": ["a/*"]}]})
        self.assertEqual(got, [("g", ["a/*"]), ("ids-frozen", ["data/ids.json", "tests/ids/"])])

    def test_untracked_and_ignored_predicted_paths(self):
        repo = os.path.join(self.d, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("mat/\n")
        os.makedirs(os.path.join(repo, "mat"))
        for n in ("mat/x.txt", "tracked.py", "loose.py"):
            with open(os.path.join(repo, n), "w", encoding="utf-8") as f:
                f.write("x\n")
        git(repo, "add", "tracked.py")
        self.task("task-1.md", "Predicted paths: mat/x.txt, tracked.py, loose.py, new.py\n")
        got = plan_rules.check_untracked(self.d, "task-1", repo)
        self.assertEqual(len(got), 2)
        self.assertIn("ignored", got[0])
        self.assertIn("untracked", got[1])

    def test_done_when_file_needs_a_producer(self):
        ph = {"dir": "01-a", "done_when": "test -f out/report.json && node --test t/a.test.js", "paths": []}
        got = plan_rules.done_when_problems(ph, self.d, [], "")
        self.assertEqual(len(got), 2)
        self.assertEqual(plan_rules.done_when_problems(ph, self.d, ["out/report.json", "t/a.test.js"], ""), [])
        self.assertEqual(plan_rules.done_when_problems(ph, self.d, ["t/a.test.js"], "ops-1 manual: writes out/report.json"), [])

    def test_done_when_ref_token_is_not_a_file(self):
        ph = {"dir": "01-a", "done_when": "git rev-parse refs/tags/x.json", "paths": []}
        self.assertEqual(plan_rules.done_when_problems(ph, self.d, [], ""), [])

    def test_release_flag_only_in_some_phases(self):
        a, b = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        for p, fl in ((a, "flags: no-release"), (b, "flags: ui")):
            os.makedirs(p)
            with open(os.path.join(p, "TASKS.md"), "w", encoding="utf-8") as f:
                f.write(fl + "\n")
        got = plan_rules.release_flag_problems([os.path.join(a, "TASKS.md"), os.path.join(b, "TASKS.md")])
        self.assertTrue(got and got[0].startswith("release:"))
        self.assertEqual(plan_rules.release_flag_problems([os.path.join(a, "TASKS.md")]), [])

    def dw(self, cmd, produced=(), lead=""):
        return plan_rules.done_when_problems({"dir": "01-a", "done_when": cmd, "paths": []}, self.d, list(produced), lead)

    def test_done_when_valid_commands_pass(self):
        for cmd in (
            "python -m pkg.mod",
            "test v1.2 = v1.2",
            "jq .a.b data.json > /dev/null",
            "make 2>&1 | tee out/build.log",
            "tool -o out/res.json",
            "ls /tmp/x/y.json",
            "cat x > 'out/q.json'",
            'echo "see docs/a.md"',
            "pytest > out/log.txt",
        ):
            self.assertEqual(plan_check.errors_of(self.dw(cmd)), [], cmd)

    def test_done_when_bare_name_is_warning_slash_path_is_error(self):
        got = self.dw("git diff --exit-code -- README.md")
        self.assertTrue(got and got[0].startswith("done:"))
        self.assertEqual(plan_check.errors_of(got), [])
        got = self.dw("test -f out/report.json")
        self.assertEqual(plan_check.errors_of(got), got)
        self.assertEqual(len(got), 1)

    def test_protected_gate_markdown_forms(self):
        with open(os.path.join(self.d, "AUDIT.md"), "w", encoding="utf-8") as f:
            f.write("- **Protected gate x**: a/*, `b.json`\n| protected gate z: q/, r.py |\n> protected gate w: __init__.py\n")
        got = plan_rules.protected_gates(self.d, {})
        self.assertEqual(got, [("x", ["a/*", "b.json"]), ("z", ["q/", "r.py"]), ("w", ["__init__.py"])])

    def test_size_counts_unique_and_dir_weight(self):
        self.task("task-1.md", "Predicted paths: " + ", ".join(["a.py"] * 30) + "\n")
        self.assertEqual(plan_rules.check_task_size(self.d, T1), [])
        self.task("task-1.md", "Predicted paths: src/, lib/*.py, a.py\n")  # 5 + 5 + 1
        self.assertEqual(plan_rules.check_task_size(self.d, T1), [])
        self.task("task-1.md", "Predicted paths: a/, b/, c/, d.py\n")  # 16: no size warning
        self.assertEqual(plan_rules.check_task_size(self.d, T1), [])

    def test_quoted_paths_with_commas(self):
        from pipeline.plan_files import paths_in

        self.assertEqual(paths_in("Predicted paths: \"a,b.txt\", c.py, 'd,e.py'\n"), ["a,b.txt", "c.py", "d,e.py"])

    def test_release_flag_final_phase_only(self):
        mds = []
        for name, fl in (("p1", "flags: no-release"), ("p2", "flags: ui"), ("p3", "flags: ui")):
            os.makedirs(os.path.join(self.d, name))
            mds.append(os.path.join(self.d, name, "TASKS.md"))
            with open(mds[-1], "w", encoding="utf-8") as f:
                f.write(fl + "\n")
        self.assertTrue(plan_rules.release_flag_problems(mds)[0].startswith("release:"))
        with open(mds[-1], "w", encoding="utf-8") as f:
            f.write("flags: no-release\n")
        self.assertEqual(plan_rules.release_flag_problems(mds), [])
        with open(mds[0], "w", encoding="utf-8") as f:
            f.write("flags: ui\n")  # only the final phase has it: no hazard
        self.assertEqual(plan_rules.release_flag_problems(mds), [])

    def test_untracked_uses_two_git_calls_per_repo(self):
        from unittest import mock

        repo = os.path.join(self.d, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        for n in ("a.py", "b.py", "c.py"):
            with open(os.path.join(repo, n), "w", encoding="utf-8") as f:
                f.write("x\n")
        git(repo, "add", "a.py")
        self.task("task-1.md", "Predicted paths: a.py, b.py, c.py, new.py\n")
        with mock.patch.object(plan_rules.gitx, "run", wraps=plan_rules.gitx.run) as run:
            got = plan_rules.check_untracked(self.d, "task-1", repo)
        self.assertEqual(len(got), 2)
        self.assertLessEqual(run.call_count, 3)  # rev-parse, ls-files -z (check-ignore --stdin -z runs apart)

    def _nested_fixture(self):
        repo = os.path.join(self.d, "repo")
        nested = os.path.join(repo, "workspace", "course-a")
        os.makedirs(os.path.join(nested, "content"))
        git(repo, "init", "-q")
        git(nested, "init", "-q")
        with open(os.path.join(nested, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("mat/\n")
        os.makedirs(os.path.join(nested, "mat"))
        for n in ("mat/x.txt", "content/tracked.jsonl", "content/loose.jsonl"):
            with open(os.path.join(nested, n), "w", encoding="utf-8") as f:
                f.write("x\n")
        git(nested, "add", "content/tracked.jsonl")
        return repo

    def test_nested_repo_paths_judged_in_nested_repo(self):
        repo = self._nested_fixture()
        pre = "workspace/course-a/"
        self.task("task-1.md", f"Predicted paths: {pre}mat/x.txt, {pre}content/tracked.jsonl, {pre}content/loose.jsonl, {pre}content/new.jsonl\n")
        got = plan_rules.check_untracked(self.d, "task-1", repo)
        self.assertEqual(len(got), 2, got)
        self.assertIn("ignored", got[0])
        self.assertIn("untracked", got[1])
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, T1, repo, "workspace/"), [])

    def test_nested_repo_missing_or_decoy_prefix(self):
        repo = self._nested_fixture()
        os.makedirs(os.path.join(repo, "workspace", "course-ax"))  # decoy: shares the prefix, holds no repo
        self.task("task-1.md", "Predicted paths: workspace/course-ax/a.jsonl\n")
        self.assertEqual(len(plan_rules.check_nested_repo_paths(self.d, T1, repo, "workspace/")), 1)
        self.task("task-2.md", "Predicted paths: workspace/none/a.jsonl\n")
        self.assertEqual(len(plan_rules.check_nested_repo_paths(self.d, T2, repo, "workspace/")), 1)

    def test_nested_repo_rule_off_without_prefix(self):
        repo = self._nested_fixture()
        self.task("task-1.md", "Predicted paths: workspace/none/a.jsonl\n")
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, T1, repo), [])  # default "" = rule off
        self.assertEqual(plan_rules.check_nested_repo_paths(self.d, T1, repo, ""), [])

    def test_nested_repo_git_calls_per_repo(self):
        from unittest import mock

        repo = self._nested_fixture()
        self.task("task-1.md", "Predicted paths: workspace/course-a/content/loose.jsonl, workspace/course-a/content/tracked.jsonl, a.py\n")
        with mock.patch.object(plan_rules.gitx, "run", wraps=plan_rules.gitx.run) as run:
            plan_rules.check_untracked(self.d, "task-1", repo)
        self.assertLessEqual(run.call_count, 6)  # per repo: rev-parse, ls-files, check-ignore

    def test_check_stage_reads_gates_once(self):
        from unittest import mock

        for i in (1, 2, 3):
            self.task(f"task-{i}.md", f"# task-{i}: a\nGoal: x\nPredicted paths: a{i}.py\n")
        self.task(
            "TASKS.md", "# Stage: s\n\n## Tasks\n- [ ] task-1 a (W1)\n- [ ] task-2 b (W1)\n- [ ] task-3 c (W1)\n\n## Graph\nwave 1: task-1, task-2, task-3\n"
        )
        with mock.patch.object(plan_check, "protected_gates", return_value=[]) as pg:
            plan_check.check_stage(self.d, self.d)
        self.assertEqual(pg.call_count, 1)


class WrittenFilesTest(unittest.TestCase):
    def test_written_files(self):
        cases = {
            "echo x > out.txt": {"out.txt"},
            "echo x >> out.txt; true": {"out.txt"},
            "echo x >out.txt": {"out.txt"},
            "cmd 2>err.log": {"err.log"},
            "cmd | tee a.log": {"a.log"},
            "cmd | tee -a a.log": {"a.log"},
            "tool -o res.json in.txt": {"res.json"},
            "tool --output=res.json": {"res.json"},
            "tool --output res.json": {"res.json"},
            "tool in.txt out.txt": set(),
            "(echo x > a.txt)": {"a.txt"},
        }
        for cmd, want in cases.items():
            self.assertEqual(plan_rules.written_files(cmd), want, cmd)


if __name__ == "__main__":
    unittest.main()


class PhaseDependsTest(unittest.TestCase):
    def test_unknown_or_later_depends_is_a_problem(self):
        from pipeline.plan_check import _phase_link_problems

        earlier = [{"dir": "01-audit", "paths": []}]
        mk = lambda dep: {"dir": "02-x", "paths": [], "depends": [dep]}  # noqa: E731
        self.assertEqual(_phase_link_problems(mk("01-audit"), earlier), [])
        self.assertEqual(_phase_link_problems(mk("audit"), earlier), [])
        for bad in ("01", "03-later", "Audit"):
            probs = _phase_link_problems(mk(bad), earlier)
            self.assertEqual(len(probs), 1, bad)
            self.assertIn("unknown or later phase", probs[0])


class SkipGateTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name

    def check(self, text):
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return plan_rules.check_skip_gate(self.d, "task-1")

    def test_skipif_and_pytest_skip_warn(self):
        for line in ("@pytest.mark.skipif(True, reason='x')", "    pytest.skip('no db')"):
            got = self.check(f"Done: {line}\n")
            self.assertEqual(len(got), 1, line)
            self.assertTrue(got[0].startswith("skip-gate: task-1 says skipif/pytest.skip:"), got[0])
            self.assertIn("exits 3", got[0])

    def test_plain_words_and_marker_fix_do_not_warn(self):
        for text in ("Done: the test is skipped in CI\n", "Done: use a deselect marker the conftest honours\n", ""):
            self.assertEqual(self.check(text), [], text)

    def test_negative_sentence_with_skip_gate_word_still_warns_only_on_the_call(self):
        self.assertEqual(self.check("Done: no skip-gate word here\n"), [])
        self.assertEqual(len(self.check("Done: skip-gate word, but pytest.skip( is used\n")), 1)
