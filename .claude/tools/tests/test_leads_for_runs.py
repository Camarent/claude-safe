"""leads_for really runs local.py (every other test sets PIPELINE_NO_LEADS): leads reach the brief, IDK gives nothing, a failure is not cached."""

import glob
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, briefs
from pipeline.configuration import ENV_NO_LEADS, PROJECT_DEFAULTS
from tests.support.tmpdirs import tracked_mkdtemp


def fake_local(stdout, returncode=0):
    return mock.patch.object(briefs, "run_py", return_value=SimpleNamespace(returncode=returncode, stdout=stdout, stderr=""))


class LeadsForRuns(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp(prefix="synthetic-project-")
        os.makedirs(os.path.join(self.d, ".work", "s"))
        with open(os.path.join(self.d, "a.py"), "w", encoding="utf-8") as f:
            f.write("def f():\n    return 1\n")
        self.a = SimpleNamespace(dir=self.d, stage="s")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV_NO_LEADS, None)  # these tests need the real path

    def test_two_leads_reach_the_brief(self):
        with fake_local("a.py:1\nb.py:2\n") as run:
            text = briefs.leads_for(self.a, "k1", "where is f")
        self.assertEqual(run.call_args[0][:3], ("search/local.py", "find", "where is f"))
        self.assertIn("Code leads", text)
        self.assertIn("a.py:1", text)
        self.assertIn("b.py:2", text)

    def test_idk_reply_gives_nothing(self):
        with fake_local("IDK"):
            self.assertEqual(briefs.leads_for(self.a, "k2", "where is f"), "")

    def test_a_failed_search_is_not_cached(self):
        with fake_local("", returncode=1):
            self.assertEqual(briefs.leads_for(self.a, "k3", "where is f"), "")
        with fake_local("a.py:1\n"):  # the model is back: the next call searches again
            self.assertIn("Code leads", briefs.leads_for(self.a, "k3", "where is f"))

    def test_changed_text_refreshes_the_cached_leads(self):
        with fake_local("old.py:1\n"):
            self.assertIn("old.py", briefs.leads_for(self.a, "k4", "where is f"))
        with fake_local("new.py:1\n"):
            self.assertIn("old.py", briefs.leads_for(self.a, "k4", "where is f"))  # same text: cached
            self.assertIn("new.py", briefs.leads_for(self.a, "k4", "where is g (task edited after a retry)"))

    def test_fix_rounds_have_their_own_lead_cache(self):
        tasks = [{"id": "task-1", "wave": 1}]
        flags = {"ui": False}
        cfg = dict(PROJECT_DEFAULTS, audit_file="AUDIT.md")
        audit_dir = self.d
        with fake_local("a.py:1\n"):
            next(audit_phases._fix_phases(self.a, cfg, flags, tasks, audit_dir, True, False, [("audit-code", "FIXES")], 1))
            next(audit_phases._fix_phases(self.a, cfg, flags, tasks, audit_dir, True, False, [("audit-code", "FIXES")], 2))
        found = glob.glob(os.path.join(self.d, ".work", "s", "leads", "leads-fix-plan*.md"))
        names = sorted(os.path.basename(p) for p in found if not p.endswith("-rel.md"))
        self.assertEqual(names, ["leads-fix-plan-1.md", "leads-fix-plan-2.md"])


if __name__ == "__main__":
    unittest.main()
