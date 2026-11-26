"""pipeline.preflight: Needs:/Mode: parsing and the probes (binary, file, gh scope, TMPDIR length), with fakes only."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import preflight

TASK_NEEDS = "Goal: x\nNeeds: bin:jq, file:scripts/dev.sh, scope:repo\nDone: ok\n"
TASK_MODE = "Goal: x\nMode: scripts/run.sh 755\nMode: tools/a.py 644\nDone: ok\n"
LONG_BASE = "/" + "d" * 70  # qrun TMPDIR under this base is over QRUN_TMP_MAX_PATH bytes
NO_TMP_ENV = {"QRUN_TMP_BASE": "/tmp"}


def fake_which(present):
    """Fake shutil.which: a name in present is found, anything else is None."""
    return lambda name: f"/usr/bin/{name}" if name in present else None


def fake_runner(rc: int | None = 0, out: str = ""):
    """Fake gh runner -> (runner, calls): every call returns (rc, out) and is recorded in calls; rc None is a missing gh."""
    calls = []

    def runner(argv):
        calls.append(tuple(argv))
        return rc, out

    return runner, calls


class NeedsModeParseTest(unittest.TestCase):
    def test_needs_lines_parses_each_kind_in_order(self):
        self.assertEqual(
            preflight.needs_lines(TASK_NEEDS),
            [("bin", "jq"), ("file", "scripts/dev.sh"), ("scope", "repo")],
        )

    def test_needs_lines_ignores_text_without_anchored_line(self):
        self.assertEqual(preflight.needs_lines("Note: Needs: bin:jq\nDone: ok\n"), [])

    def test_needs_lines_drops_unknown_kinds_and_empty_values(self):
        self.assertEqual(preflight.needs_lines("Needs: net:x, bin:, bin:git\n"), [("bin", "git")])

    def test_mode_lines_parses_path_and_mode(self):
        self.assertEqual(preflight.mode_lines(TASK_MODE), [("scripts/run.sh", "755"), ("tools/a.py", "644")])

    def test_mode_lines_ignores_non_octal(self):
        self.assertEqual(preflight.mode_lines("Mode: a.sh 7x5\n"), [])


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "repo")
        self.stage = os.path.join(self._tmp.name, "stage")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(self.stage)
        open(os.path.join(self.repo, "scripts", "dev.sh"), "w", encoding="utf-8").close()

    def write_task(self, text, name="task-1.md"):
        with open(os.path.join(self.stage, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_probe(self, present=("jq", preflight.GH_TOOL), gh=None, env=NO_TMP_ENV):
        runner, calls = gh if gh else fake_runner(rc=1)
        problems = preflight.run(self.repo, self.stage, runner=runner, which=fake_which(present), env=env)
        return problems, calls

    def test_missing_binary_is_a_problem_with_fix(self):
        self.write_task("Needs: bin:jq, bin:nosuchtool\n")
        problems, _ = self.run_probe(present=("jq",), gh=fake_runner(rc=1))
        self.assertEqual(problems, [("binary not on PATH: nosuchtool", "install nosuchtool and put it on PATH")])

    def test_present_binary_is_not_a_problem(self):
        self.write_task("Needs: bin:jq\n")
        problems, _ = self.run_probe(present=("jq",))
        self.assertEqual(problems, [])

    def test_missing_file_is_a_problem_with_fix(self):
        self.write_task("Needs: file:scripts/gone.sh\n")
        problems, _ = self.run_probe()
        self.assertEqual(problems, [("file missing in repo: scripts/gone.sh", f"create scripts/gone.sh in {self.repo}")])

    def test_each_need_probed_once_across_task_files(self):
        self.write_task("Needs: bin:nosuchtool\n", name="task-1.md")
        self.write_task("Needs: bin:nosuchtool\n", name="task-2.md")
        problems, _ = self.run_probe()
        self.assertEqual(len(problems), 1)

    def test_all_problems_written_to_one_blocked_file(self):
        self.write_task("Needs: bin:nosuchtool, file:scripts/gone.sh\n")
        problems, _ = self.run_probe(gh=fake_runner(rc=1))
        self.assertEqual(len(problems), 2)
        with open(os.path.join(self.stage, preflight.BLOCKED_FILE), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("binary not on PATH: nosuchtool", text)
        self.assertIn("file missing in repo: scripts/gone.sh", text)
        self.assertIn("fix: install nosuchtool and put it on PATH", text)

    def test_no_problem_writes_no_blocked_file(self):
        self.write_task("Needs: bin:jq\n")
        problems, _ = self.run_probe()
        self.assertEqual(problems, [])
        self.assertFalse(os.path.exists(os.path.join(self.stage, preflight.BLOCKED_FILE)))

    def test_scope_granted_is_not_a_problem(self):
        self.write_task("Needs: scope:repo\n")
        problems, calls = self.run_probe(gh=fake_runner(out="HTTP/2 200\nX-OAuth-Scopes: repo, read:org\n"))
        self.assertEqual(problems, [])
        self.assertEqual(calls, [preflight.GH_SCOPE_ARGV])

    def test_scope_missing_is_a_problem_with_fix(self):
        self.write_task("Needs: scope:workflow\n")
        problems, _ = self.run_probe(gh=fake_runner(out="X-OAuth-Scopes: repo\n"))
        self.assertEqual(problems, [("gh token lacks scope workflow", "gh auth refresh -s workflow")])

    def test_fine_grained_token_without_header_is_unverifiable_not_blocked(self):
        self.write_task("Needs: scope:repo\n")
        problems, _ = self.run_probe(gh=fake_runner(out="HTTP/2 200\n"))
        self.assertEqual(problems, [])

    def test_gh_missing_is_unverifiable_not_raised(self):
        self.write_task("Needs: scope:repo\n")
        runner, calls = fake_runner(out="X-OAuth-Scopes: none\n")
        problems = preflight.run(self.repo, self.stage, runner=runner, which=fake_which(()), env=NO_TMP_ENV)
        self.assertEqual(problems, [])
        self.assertEqual(calls, [])

    def test_gh_failing_is_unverifiable_not_raised(self):
        self.write_task("Needs: scope:repo\n")
        problems, _ = self.run_probe(gh=fake_runner(rc=None, out=""), present=("gh",))
        self.assertEqual(problems, [])

    def test_long_scratch_base_is_a_problem_with_fix(self):
        self.write_task("Needs: bin:jq\n")
        problems, _ = self.run_probe(env={"QRUN_TMP_BASE": LONG_BASE})
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][1], "export QRUN_TMP_BASE=/tmp")
        self.assertIn("over 60", problems[0][0])

    def test_short_scratch_base_is_not_a_problem(self):
        self.write_task("Needs: bin:jq\n")
        problems, _ = self.run_probe(env={"QRUN_TMP_BASE": "/tmp"})
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
