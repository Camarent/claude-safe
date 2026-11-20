"""Synthetic tests: which phases get audited."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan
from pipeline.phase_audit import audit_due
from tests.support.tmpdirs import tracked_mkdtemp


class AuditDue(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        cli_plan.main(["plan-init", "--feature", "F", "--goal", "g", "--dir", self.d])
        prev = None
        for n in "abcd":
            cli_plan.main(
                [
                    "phase",
                    "--feature",
                    "F",
                    "--name",
                    n,
                    "--goal",
                    n,
                    "--done-when",
                    "true",
                    "--paths",
                    f"src/{n}",
                    *(["--depends", prev] if prev else []),
                    "--dir",
                    self.d,
                ]
            )
            prev = n

    def folder(self, i):
        return os.path.join(self.d, ".work", "F", "phases", ["01-a", "02-b", "03-c", "04-d"][i])

    def tasks(self, i, n):
        os.makedirs(self.folder(i), exist_ok=True)
        with open(os.path.join(self.folder(i), "TASKS.md"), "w") as f:
            f.write(
                "| task | what | owner | price | review |\n|---|---|---|---|---|\n"
                + "".join(f"| task-{k} | x | dev | 1 | wave 1, haiku |\n" for k in range(1, n + 1))
                + "\n## Graph\nwave 1: "
                + ", ".join(f"task-{k}" for k in range(1, n + 1))
                + "\n"
            )

    def test_size_decides_for_middle_phases_and_last_is_always_audited(self):
        self.tasks(0, 40)
        self.assertTrue(audit_due(self.folder(0), 40, 25, self.d))  # a big middle phase is audited
        self.tasks(0, 5)
        self.assertFalse(audit_due(self.folder(0), 5, 25, self.d))  # a small one is not
        self.tasks(1, 10)
        self.assertFalse(audit_due(self.folder(1), 10, 25, self.d))  # 5 + 10 pending
        self.tasks(2, 12)
        self.assertTrue(audit_due(self.folder(2), 12, 25, self.d))  # 5 + 10 + 12 reaches the threshold
        self.assertTrue(audit_due(self.folder(3), 1, 25, self.d))  # the last phase: always

    def test_last_task_phase_before_finish_is_not_audited_but_finish_is(self):
        cli_plan.main(["phase", "--feature", "F", "--name", "finish", "--goal", "f", "--done-when", "true", "--paths", "x", "--depends", "d", "--dir", self.d])
        self.tasks(3, 40)
        self.assertFalse(audit_due(self.folder(3), 40, 25, self.d))  # 04-d is the last task phase: finish audits it
        fin = os.path.join(self.d, ".work", "F", "phases", "05-finish")
        self.assertTrue(audit_due(fin, 1, 25, self.d))

    def test_an_audited_phase_resets_the_count_and_plain_stages_are_always_audited(self):
        self.tasks(0, 20)
        self.tasks(1, 10)
        os.makedirs(os.path.join(self.folder(0), "audits"))
        open(os.path.join(self.folder(0), "audits", "audit-sec.md"), "w").close()
        self.assertFalse(audit_due(self.folder(1), 10, 25, self.d))  # 20 were audited: only 10 pending
        plain = os.path.join(self.d, ".work", "plain")
        os.makedirs(plain)
        self.assertTrue(audit_due(plain, 1, 25, self.d))


if __name__ == "__main__":
    unittest.main()
