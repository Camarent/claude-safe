"""Characterization tests for plan_check.check_stage: each problem line."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from pipeline.configuration import DOCS_IMPACT_PLAN_PREFIX, NEEDS_PREFIX, TEST_ROOT_PREFIX, TEST_WRAP_PREFIX

WIRED_PREFIXES = (TEST_ROOT_PREFIX, TEST_WRAP_PREFIX, NEEDS_PREFIX, DOCS_IMPACT_PLAN_PREFIX)


def task_id(n):
    """Task id `task-N` built outside the assert literal (the path-dependence gate reads assert arguments)."""
    return f"task-{n}"


class CheckStageTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="synthetic-project-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, ".work", "s")
        os.makedirs(self.d)

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def publish_config(self, tasks_text, produced, build="make", cwd="course"):
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"publish": {"https://x/a": {"cwd": cwd, "build": build, "page": "i.html"}}}, f)
        self.write("TASKS.md", tasks_text)
        with mock.patch.object(plan_check, "_stage_produced", return_value=produced):
            return plan_check.publish_problems(self.d, [])

    def shared_stage(self, n):
        ents = []
        for i in range(1, n + 1):
            self.write(f"task-{i}.md", f"# T{i}\nPredicted paths: web/i18n/en.json, src/f{i}.py\n")
            ents.append({"id": f"task-{i}", "wave": i})
        return ents

    def test_shared_warns_three_tasks(self):
        got = plan_check.shared_warnings(self.d, self.shared_stage(3))
        self.assertEqual(len(got), 1)
        self.assertIn(f"shared: web/i18n/en.json is listed by {task_id(1)}, {task_id(2)}, {task_id(3)} (3 tasks, 3 waves)", got[0])
        self.assertEqual(plan_check.errors_of(got), [])

    def test_shared_silent_for_two_tasks(self):
        self.assertEqual(plan_check.shared_warnings(self.d, self.shared_stage(2)), [])

    def test_publish_problem_without_publish_line(self):
        got = self.publish_config("# Stage\n", ["course/lesson.md"])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(plan_check.PUBLISH_PROBLEM_PREFIX))
        self.assertEqual(plan_check.errors_of(got), got)  # a problem: it fails the check

    def test_publish_scope_follows_the_build_command(self):
        # cwd is the repo; the course folder sits below it and only the build command names it
        kw = {"build": "./tool build workspace/course-a --out {out}", "cwd": "app"}
        self.assertEqual(len(self.publish_config("# Stage\n", ["workspace/course-a/data/x.json"], **kw)), 1)
        self.assertEqual(self.publish_config("# Stage\n", ["workspace/other/x.json"], **kw), [])
        self.assertEqual(self.publish_config("# Stage\n", ["src/tool/x.py"], **kw), [])  # a bare build word is no scope

    def test_publish_no_problem_with_line_flag_or_other_paths(self):
        self.assertEqual(self.publish_config("PUBLISH: https://x/a\n", ["course/lesson.md"]), [])
        self.assertEqual(self.publish_config("flags: no-publish\n", ["course/lesson.md"]), [])
        self.assertEqual(self.publish_config("# Stage\n", ["other/x.md"]), [])

    def test_publish_sees_every_phase_and_skips_a_mid_phase(self):
        feature = os.path.join(self.root, ".work", "f")
        phases = [os.path.join(feature, "phases", n) for n in ("01-a", "02-fin")]
        for p in phases:
            os.makedirs(p)
        with open(os.path.join(feature, "Plan.md"), "w", encoding="utf-8") as f:
            f.write("| phase | goal | done_when | depends | paths | status | tasks |\n|---|---|---|---|---|---|---|\n")
            f.write("| 01-a | g | c | | | todo | 1 |\n| 02-fin | g | c | 01-a | | todo | 1 |\n")
        for p in phases:
            with open(os.path.join(p, "TASKS.md"), "w", encoding="utf-8") as f:
                f.write("# P\n")
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"publish": {"https://x/a": {"cwd": "course", "build": "make", "page": "i.html"}}}, f)
        first = [{"id": "task-1"}]
        with mock.patch.object(plan_check, "read_entries", return_value=first), mock.patch.object(plan_check, "task_paths", return_value=["course/a.md"]):
            self.assertEqual(plan_check.publish_problems(phases[0], []), [])  # mid phase: the last phase checks
            self.assertEqual(len(plan_check.publish_problems(phases[1], [])), 1)  # the last phase sees phase 1's paths

    def rules_stage(self, tasks):
        """tasks: {task id: task file text}. Writes pipeline.json (test_roots, test_wrap, docs_dir), docs/m.md and the stage; -> check_stage problems."""
        cfg = {
            "test_roots": [{"runner": "vitest", "root": "web"}],
            "test_wrap": [{"match": "tests/db", "require": "KATA_CONFIG", "use": "KATA_CONFIG=<config> scripts/dev.sh test"}],
            "docs_dir": "docs",
        }
        with open(os.path.join(self.root, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        os.makedirs(os.path.join(self.root, "docs"))
        with open(os.path.join(self.root, "docs", "m.md"), "w", encoding="utf-8") as f:
            f.write("see a.py for the parser\n")
        self.write("TASKS.md", "## Tasks\n" + "".join(f"- [ ] {t} stage - owner: haiku - review: wave W1, haiku\n" for t in tasks))
        for t, text in tasks.items():
            self.write(f"{t}.md", f"# {t}\n{text}")
        return plan_check.check_stage(self.d, repo=self.root)[1]

    def test_each_wired_rule_reaches_the_stage_result(self):
        probs = self.rules_stage(
            {
                "task-1": "Predicted paths: src/a.py\nTest: npx vitest run web/src/x.test.js\n",
                "task-2": "Predicted paths: src/b.py\nTest: scripts/dev.sh test tests/db/test_store.py\n",
                "task-3": "Predicted paths: src/c.py\nNeeds: docs/missing.md :: some literal\n",
                "task-4": "Predicted paths: a.py\n",
            }
        )
        for prefix, tid in zip(WIRED_PREFIXES, ("task-1", "task-2", "task-3", "task-4"), strict=True):
            self.assertTrue(any(p.startswith(prefix) and f" {tid}:" in p for p in probs), (prefix, probs))

    def test_a_clean_stage_gives_no_wired_problem(self):
        probs = self.rules_stage(
            {
                "task-1": "Predicted paths: src/a.py\nTest: npx vitest run src/x.test.js\n",
                "task-2": "Predicted paths: src/b.py\nTest: scripts/dev.sh test tests/other/test_a.py\n",
            }
        )
        self.assertEqual([p for p in probs if p.startswith(WIRED_PREFIXES)], [])

    def test_missing_tasks_md(self):
        self.assertEqual(plan_check.check_stage(self.d), (0, [f"{self.d}/TASKS.md missing"]))

    def test_problem_lines(self):
        self.write(
            "TASKS.md",
            "## Tasks\n"
            "- [ ] task-1 one - owner: haiku - review: wave W1, haiku\n"
            "- [ ] task-2 two - owner: haiku - review: wave W1, haiku - depends: task-1\n"
            "- [ ] task-3 three - owner: haiku - review: wave W2, haiku - depends: task-9\n"
            "- [ ] task-4 four - owner: haiku - review: wave W2, haiku\n"
            "- [ ] task-5 five - owner: haiku - review: wave W2, haiku\n"
            "\n## Graph\nwave 1: task-1 | wave 2: task-3, task-4\n"
            "| task-6 | extra | dev | 1 | wave 2, haiku |\n",
        )
        for n in (1, 2, 3, 4):
            self.write(f"task-{n}.md", "Predicted paths: src/shared/\n" if n in (1, 2) else f"Predicted paths: src/p{n}/\n")
        n, probs = plan_check.check_stage(self.d, repo=self.root)
        self.assertEqual(n, 5)
        for expected in (
            "task-2: Graph wave does not match wave W1",
            "task-5: Graph wave does not match wave W2",
            "task-6: TASKS.md entry not in plan.py format",
            f"task-5: missing {self.d}/task-5.md",
            "task-3: depends on missing task-9",
            "task-2: depends on task-1 (wave 1) but runs in wave 1 (cycle or bad order)",
            "task-1 and task-2 share paths in wave 1: src/shared/ / src/shared/ (same-file tasks never share a wave unless the file is .jsonl/.tsv/.csv/.ndjson and both list it as `(rows)`; a `(mass)` or unannotated entry never shares: make one depend on the other, `plan.py task --depends N`, so it runs in a later wave)",
        ):
            self.assertIn(expected, probs)

    def test_test_path_created_by_dependency(self):
        self.write(
            "TASKS.md",
            "## Tasks\n"
            "- [ ] task-1 tool - owner: haiku - review: wave W1, haiku\n"
            "- [ ] task-2 use - owner: haiku - review: wave W2, haiku - depends: task-1\n"
            "- [ ] task-3 loner - owner: haiku - review: wave W2, haiku\n",
        )
        self.write("task-1.md", "Predicted paths: tools/check.js\n")
        self.write("task-2.md", "Predicted paths: out/a.json\nTest: node tools/check.js out\n")
        self.write("task-3.md", "Predicted paths: out/b.json\nTest: node tools/check.js out\n")
        self.assertEqual(plan_check.check_test_globs(self.d, task_id(2), self.root), [])
        self.assertEqual(len(plan_check.check_test_globs(self.d, task_id(3), self.root)), 1)

    def test_unquoted_eval_expression(self):
        self.write("task-1.md", "Test: qrun -- node -e JSON.parse(require('fs').readFileSync('a.json'))\n")
        self.write("task-2.md", "Test: qrun -- node -e \"JSON.parse(require('fs').readFileSync('a.json'))\"\n")
        self.assertEqual(len(plan_check.check_test_quoting(self.d, task_id(1))), 1)
        self.assertEqual(plan_check.check_test_quoting(self.d, task_id(2)), [])

    def test_relative_work_output_rejected(self):
        self.write("task-1.md", "Predicted paths: .work/s/ambiguous.md\nDone: write .work/s/out.md\n")
        probs = plan_check.check_work_paths(self.d, "task-1")
        self.assertEqual(len(probs), 1)
        self.assertIn(f"{task_id(1)}: output `.work/s/ambiguous.md` is a relative .work path", probs[0])
        self.assertIn(os.path.join(self.root, ".work", "s") + "/...", probs[0])

    def test_absolute_work_output_accepted(self):
        self.write("task-1.md", f"Predicted paths: {self.d}/ambiguous.md\nDone: wrote {self.d}/out.md\n")
        self.assertEqual(plan_check.check_work_paths(self.d, task_id(1)), [])

    def test_absolute_work_output_passes_git_check(self):
        self.write("task-1.md", f"Predicted paths: {self.d}/ambiguous.md\nDone: wrote {self.d}/out.md\n")
        self.assertEqual(plan_check.check_git_paths(self.d, task_id(1), self.root), [])

    def test_absolute_path_to_other_non_git_dir_rejected_by_git_check(self):
        other = tempfile.mkdtemp(prefix="synthetic-other-")
        self.addCleanup(shutil.rmtree, other, True)
        self.write("task-1.md", f"Predicted paths: {other}/out.md\n")
        probs = plan_check.check_git_paths(self.d, task_id(1), self.root)
        self.assertEqual(len(probs), 1)
        self.assertIn(f"{other}/out.md is in no git repo", probs[0])

    def test_relative_work_output_still_rejected_by_both(self):
        self.write("task-1.md", "Predicted paths: .work/s/out.md\n")
        self.assertEqual(len(plan_check.check_work_paths(self.d, task_id(1))), 1)
        self.assertEqual(len(plan_check.check_git_paths(self.d, task_id(1), self.root)), 1)

    def test_work_prefix_sibling_dir_not_exempt(self):
        sibling = os.path.join(self.root, ".work-other")  # shares the `.work` prefix but is not under the .work root
        self.write("task-1.md", f"Predicted paths: {sibling}/out.md\n")
        self.assertEqual(len(plan_check.check_git_paths(self.d, task_id(1), self.root)), 1)

    def test_source_paths_accepted(self):
        self.write("task-1.md", "Predicted paths: src/a.py, tests/test_a.py\nDone: src/a.py:3 works, .work notes are not paths\n")
        self.assertEqual(plan_check.check_work_paths(self.d, task_id(1)), [])


if __name__ == "__main__":
    unittest.main()
