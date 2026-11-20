"""Auditor reply saved to audits/<report>.md when the file is missing or empty; re-audit rebuilds its context."""

import os
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import audit_phases
from pipeline.audit_phases import audit_report_name, reaudit_context_steps, save_audit_reply


class SaveReply(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.w = td.name

    def path(self, name):
        return os.path.join(self.w, "audits", name)

    def test_names(self):
        self.assertEqual(audit_report_name("audit-sec"), "audit-sec.md")
        self.assertEqual(audit_report_name("audit-arch-2"), "audit-arch-2.md")
        self.assertEqual(audit_report_name("audit-arch-opus"), "audit-arch.md")
        self.assertIsNone(audit_report_name("final-test"))
        self.assertIsNone(audit_report_name(None))

    def test_missing_saved(self):
        self.assertEqual(save_audit_reply(self.w, "audit-sec", "VERDICT: CLEAN"), self.path("audit-sec.md"))
        with open(self.path("audit-sec.md")) as f:
            self.assertEqual(f.read(), "VERDICT: CLEAN")

    def test_empty_saved_existing_kept(self):
        os.makedirs(os.path.join(self.w, "audits"))
        with open(self.path("audit-sec.md"), "w") as f:
            f.write("  \n")
        self.assertTrue(save_audit_reply(self.w, "audit-sec", "reply"))
        self.assertIsNone(save_audit_reply(self.w, "audit-sec", "other"))
        with open(self.path("audit-sec.md")) as f:
            self.assertEqual(f.read(), "reply")

    def test_blank_reply_and_non_audit(self):
        self.assertIsNone(save_audit_reply(self.w, "audit-sec", " "))
        self.assertIsNone(save_audit_reply(self.w, "dev-1", "x"))
        self.assertFalse(os.path.exists(self.path("audit-sec.md")))


class ReauditContext(unittest.TestCase):
    def test_steps(self):
        a = SimpleNamespace(stage="S", dir="/p", feature=None)
        cfg = {"branch_release": "master", "repo": ""}
        with unittest.mock.patch.object(audit_phases, "audit_base", return_value="B"):
            sts = reaudit_context_steps(a, cfg, "/p", True, "-2")
            only = reaudit_context_steps(a, cfg, "/p", False, "-2")
        self.assertEqual([s["id"] for s in sts], ["audit-context-2", "plan-vs-changes-2"])
        self.assertEqual([s["id"] for s in only], ["plan-vs-changes-2"])
        self.assertIn("--out .work/S/audits/audit-context.md", sts[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
