"""Plan-time preflight: Test line forms the sandbox denies, and tests outside Predicted paths that mention a task symbol."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brieflint
from pipeline import plan_check


class PreflightTest(unittest.TestCase):
    def test_test_line_prefix(self):
        bad = brieflint.lint("Test: DOJO_KIT_ROOT=$PWD python3 qrun.py -- node --test a.js")
        self.assertTrue(any("VAR= prefix" in m for m in bad))
        ok = brieflint.lint("Test: python3 -I /root/.claude/tools/qrun.py -- node --test a.js")
        self.assertFalse(any("VAR= prefix" in m for m in ok))

    def test_test_line_compound(self):
        self.assertTrue(any("VAR= prefix" in m for m in brieflint.lint("Test: cd web && node --test a.js")))

    def test_test_ref_outside_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "web", "tests"))
            open(os.path.join(d, "web", "tests", "clip.test.js"), "w").write("assert(clipMeta(1))")
            open(os.path.join(d, "task-1.md"), "w").write("Goal: `clipMeta` gets a field\nPredicted paths: web/audio.js\nDone: ok\n")
            got = plan_check.check_test_refs(d, "task-1", d)
            self.assertEqual(len(got), 1)
            self.assertIn("web/tests/clip.test.js", got[0])
            open(os.path.join(d, "task-1.md"), "w").write("Goal: `clipMeta` gets a field\nPredicted paths: web/audio.js, web/tests/clip.test.js\nDone: ok\n")
            self.assertEqual(plan_check.check_test_refs(d, "task-1", d), [])
            open(os.path.join(d, "task-1.md"), "w").write(
                "Goal: `clipMeta` gets a field\nPredicted paths: web/audio.js\nNote: web/tests/clip.test.js stays, it only reads it\nDone: ok\n"
            )
            self.assertEqual(plan_check.check_test_refs(d, "task-1", d), [])

    def test_denied_command_prefix_in_test_line(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "task-1.md"), "w").write("Test: python3 -I qrun.py -- true && ./dojo build x\n")
            denied = ["rustup", "./dojo build"]
            got = plan_check.check_denied_commands(d, "task-1", denied)
            self.assertEqual(len(got), 1)
            self.assertIn("./dojo build", got[0])
            self.assertEqual(plan_check.check_denied_commands(d, "task-1", []), [])  # unset: no check
            open(os.path.join(d, "task-1.md"), "w").write("Test: python3 -I qrun.py -- pytest rustup_notes.py\n")
            self.assertEqual(plan_check.check_denied_commands(d, "task-1", denied), [])

    def test_denied_commands_come_from_pipeline_json(self):
        with tempfile.TemporaryDirectory() as d:
            stage = os.path.join(d, ".work", "s")
            os.makedirs(stage)
            self.assertEqual(plan_check._preflight_denied(stage), [])
            open(os.path.join(d, "pipeline.json"), "w").write('{"preflight_denied": ["chmod"]}')
            self.assertEqual(plan_check._preflight_denied(stage), ["chmod"])

    def test_predicted_path_outside_git(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(os.path.join(repo, "web"))
            subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
            open(os.path.join(d, "task-1.md"), "w").write("Predicted paths: web/new.js, ../outside/x.txt\n")
            got = plan_check.check_git_paths(d, "task-1", repo)
            self.assertEqual(len(got), 1)
            self.assertIn("../outside/x.txt", got[0])

    def test_warning_count_is_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            for goal, n in (("fix 18 clippy warnings in a.rs", 0), ("fix 40 clippy warnings in a.rs", 1), ("Goal without a count", 0)):
                open(os.path.join(d, "task-1.md"), "w").write(f"Goal: {goal}\n")
                got = plan_check.check_warning_count(d, "task-1")
                self.assertEqual(len(got), n)
            self.assertEqual(plan_check.errors_of([*got, "size: x"]), [])

    def test_repo_prefix_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "kit")
            os.makedirs(os.path.join(d, ".work", "f"))
            os.makedirs(repo)
            tf = os.path.join(d, ".work", "f")
            for body, n in (
                ("Predicted paths: kit/web/a.js\nPattern: kit/web/a.js:1\n", 1),
                ("Predicted paths: web/a.js\nTest: python3 qrun.py -- node --test web/a.test.js\n", 0),
                ("Predicted paths: web/a.js\nTest: node --test mykit/x.js\n", 0),
            ):
                open(os.path.join(tf, "task-1.md"), "w").write(body)
                self.assertEqual(len(plan_check.check_repo_prefix(tf, "task-1", repo)), n, body)

    def test_repo_phase_strips_prefix(self):
        from pipeline import cli_feature

        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "pipeline.json"), "w").write('{"repo": "kit"}')
            a = type("A", (), {"dir": d})()
            ph = {"dir": "01-x", "paths": ["kit/crates", "kit/a.rs", "other/b", "kit"]}
            self.assertEqual(cli_feature.repo_phase(a, ph)["paths"], ["crates", "a.rs", "other/b", "."])

    def test_done_when_must_be_shell(self):
        self.assertTrue(plan_check.is_shell("cargo clippy --all-targets -- -D warnings && ! grep -rn 'x' crates"))
        self.assertFalse(plan_check.is_shell("clippy, eslint (every course) report 0 warnings; JS branch coverage >= 89.56"))


if __name__ == "__main__":
    unittest.main()
