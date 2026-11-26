"""Acceptance timing flag: a checklist command that starts with the project test command gets the flag appended; its full output is kept in the stage reports folder."""

import json
import os
import sys
import unittest

# the sys.path line below must run before the pipeline import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import acceptance
from pipeline.configuration import ACCEPTANCE_FILE, DONE, FAIL, REPORTS_DIR, WORK_DIR, work_branch
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp

PLAN = "# Plan: f\n\n## Acceptance checklist\n- cmd: `make test -q`\n- cmd: echo hi\n\n## Phases\n"
PLAN_FAIL = "# Plan: f\n\n## Acceptance checklist\n- cmd: `make test -q`\n\n## Phases\n"
PREP = ["make build"]
FLAG = "--durations=10"


def make_project_dir(plan, config=None):
    d = tracked_mkdtemp(prefix="accept-timing-")
    git(d, "init", "-q")
    with open(os.path.join(d, "f.txt"), "w") as f:
        f.write("x")
    git(d, "add", "f.txt")
    git(d, "commit", "-qm", "init")
    git(d, "branch", work_branch({}, "f"))
    os.makedirs(os.path.join(d, ".work", "f"))
    with open(os.path.join(d, ".work", "f", "Plan.md"), "w") as f:
        f.write(plan)
    if config is not None:
        with open(os.path.join(d, "pipeline.json"), "w") as f:
            json.dump(config, f)
    return d


def reports_dir(d):
    return os.path.join(d, WORK_DIR, "f", REPORTS_DIR)


class TimingFlagTest(unittest.TestCase):
    def config(self, **extra):
        return {"test_command_hint": "make test", "acceptance_timing_flag": FLAG, **extra}

    def test_flag_is_appended_to_the_project_test_command_only(self):
        d = make_project_dir(PLAN, self.config())
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append(cmd)
            return 0, "ok"

        self.assertEqual(acceptance.run(d, "f", "f", runner)[0], DONE)
        self.assertEqual(seen, [f"make test -q {FLAG}", "echo hi"])

    def test_acceptance_md_shows_the_command_as_written(self):
        d = make_project_dir(PLAN, self.config())
        acceptance.run(d, "f", "f", lambda cmd, cwd, timeout: (0, "ok"))
        md = open(os.path.join(d, ".work", "f", ACCEPTANCE_FILE)).read()
        self.assertIn("PASS exit 0: `make test -q`", md)
        self.assertNotIn(FLAG, md)

    def test_timing_log_keeps_the_full_output_of_a_passing_flagged_command(self):
        d = make_project_dir(PLAN, self.config())

        def runner(cmd, cwd, timeout):
            return 0, "line one\nline two\nline three\nline four"

        acceptance.run(d, "f", "f", runner)
        with open(os.path.join(reports_dir(d), "acceptance-timings-1.log")) as f:
            self.assertEqual(f.read(), "line one\nline two\nline three\nline four\n")

    def test_timing_log_is_written_for_a_failing_flagged_command_too(self):
        d = make_project_dir(PLAN_FAIL, self.config())
        verdict, _ = acceptance.run(d, "f", "f", lambda cmd, cwd, timeout: (1, "boom"))
        self.assertEqual(verdict, FAIL)
        with open(os.path.join(reports_dir(d), "acceptance-timings-1.log")) as f:
            self.assertEqual(f.read(), "boom\n")

    def test_unset_flag_or_hint_changes_nothing(self):
        for config in (None, {"test_command_hint": "make test"}, {"acceptance_timing_flag": FLAG}):
            d = make_project_dir(PLAN, config)
            seen: list[str] = []

            def runner(cmd, cwd, timeout, seen=seen):
                seen.append(cmd)
                return 0, "ok"

            acceptance.run(d, "f", "f", runner)
            self.assertEqual(seen, ["make test -q", "echo hi"], config)
            self.assertFalse(os.path.exists(reports_dir(d)), config)

    def test_prep_command_is_not_flagged(self):
        d = make_project_dir(PLAN, self.config(acceptance_prep=PREP))
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append(cmd)
            return 0, "ok"

        acceptance.run(d, "f", "f", runner)
        self.assertEqual(seen, ["make build", f"make test -q {FLAG}", "echo hi"])

    def test_timeout_is_passed_through_unchanged(self):
        d = make_project_dir(PLAN, self.config())
        timeouts = []

        def runner(cmd, cwd, timeout):
            timeouts.append(timeout)
            return 0, "ok"

        acceptance.run(d, "f", "f", runner)
        self.assertEqual(set(timeouts), {acceptance.ACCEPTANCE_CMD_TIMEOUT_S})
