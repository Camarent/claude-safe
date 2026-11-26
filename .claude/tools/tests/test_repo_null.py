"""pipeline.json `"repo": null` (autodetect) must not crash plan check or the plan code context."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_run


class RepoNullTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.ws, True)  # one dir per test, gone after it
        with open(os.path.join(self.ws, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"repo": None}, f)
        self.stage = os.path.join(self.ws, ".work", "feat")
        os.makedirs(self.stage)

    def test_repo_root_autodetects_when_repo_null(self):
        self.assertEqual(plan_check._repo_root(self.stage), self.ws)

    def test_code_context_repo_null_passes_workspace(self):
        a = type("A", (), {"dir": self.ws, "feature": "feat", "stage": "feat"})()
        env = mock.patch.dict(os.environ)  # test_plan sets PLAN_NO_CONTEXT for the whole process
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PLAN_NO_CONTEXT", None)
        seen = {}

        def fake_goal_context(repo, goal, cfg, explore_log=None):
            seen["repo"] = repo
            return ""

        import localmodel.plan_context as pc

        with mock.patch.object(pc, "goal_context", side_effect=fake_goal_context), mock.patch("search.ctx_config.ensure_index"):
            plan_run.code_context(a, "goal")
        self.assertEqual(seen.get("repo"), self.ws)


if __name__ == "__main__":
    unittest.main()
