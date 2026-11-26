"""Acceptance run: the log of a failed command is copied to the stage reports folder before the temp checkout is dropped."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import acceptance
from pipeline.configuration import ACCEPTANCE_TMP_PREFIX, DONE, FAIL, REPORTS_DIR, WORK_DIR
from tests.test_acceptance import PLAN, make_repo, worktrees

REPORTS = os.path.join(WORK_DIR, "f", REPORTS_DIR)  # relative to the workspace made by make_repo


class FailedLogTest(unittest.TestCase):
    def test_failed_command_log_is_copied_to_reports_before_the_checkout_is_dropped(self):
        d = make_repo(PLAN)
        seen = {}

        def runner(cmd, cwd, timeout):
            if cmd != "false":
                return 0, "ok"
            log = os.path.join(cwd, "qrun-1.log")  # a log inside the temp checkout: it goes with the checkout
            with open(log, "w", encoding="utf-8") as f:
                f.write("full output of false\n")
            seen["cwd"] = cwd
            return 1, f"FAIL exit 1 (log: {log})\nboom"

        v, _ = acceptance.run(d, "f", "f", runner)
        self.assertEqual(v, FAIL)
        self.assertFalse(os.path.exists(seen["cwd"]))  # the checkout is gone
        self.assertEqual(len(worktrees(d)), 1)
        kept = os.listdir(os.path.join(d, REPORTS))
        self.assertEqual(kept, ["acceptance-1-qrun-1.log"])
        with open(os.path.join(d, REPORTS, kept[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), "full output of false\n")

    def test_failed_command_without_a_named_log_keeps_its_output(self):
        d = make_repo("## Acceptance checklist\n- cmd: bad\n")
        acceptance.run(d, "f", "f", lambda c, w, t: (2, "boom only"))
        self.assertEqual(os.listdir(os.path.join(d, REPORTS)), ["acceptance-1.log"])
        with open(os.path.join(d, REPORTS, "acceptance-1.log"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "boom only\n")

    def test_passing_commands_keep_no_log(self):
        d = make_repo("## Acceptance checklist\n- cmd: good\n")
        self.assertEqual(acceptance.run(d, "f", "f", lambda c, w, t: (0, "fine"))[0], DONE)
        self.assertFalse(os.path.exists(os.path.join(d, REPORTS)))

    def test_temp_checkout_uses_the_configured_prefix(self):
        d = make_repo("## Acceptance checklist\n- cmd: where\n")
        names = []

        def runner(cmd, cwd, timeout):
            names.append(os.path.basename(cwd))
            return 0, ""

        acceptance.run(d, "f", "f", runner)
        self.assertTrue(names[0].startswith(ACCEPTANCE_TMP_PREFIX))


if __name__ == "__main__":
    unittest.main()
