"""configuration.project(): defaults, pipeline.json overlay, broken file ignored."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import configuration as cfg
from tests.support.tmpdirs import tracked_mkdtemp


class ProjectTest(unittest.TestCase):
    def test_defaults_without_file(self):
        c = cfg.project(tracked_mkdtemp())
        self.assertEqual((c["branch_work"], c["branch_release"], c["test_env"], c["repo"]), ("stage/{feature}", "master", {}, None))

    def test_file_overrides_defaults(self):
        d = tracked_mkdtemp()
        with open(os.path.join(d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"branch_work": "work", "test_env": {"X": "1"}}, f)
        c = cfg.project(d)
        self.assertEqual((c["branch_work"], c["branch_release"], c["test_env"]), ("work", "master", {"X": "1"}))

    def test_removed_advice_key_still_loads(self):
        """`advice` (local advice step, removed) in an old pipeline.json is an unknown key: kept, never an error, never read."""
        d = tracked_mkdtemp()
        Path(os.path.join(d, "pipeline.json")).write_text('{"advice": true}', encoding="utf-8")
        self.assertIs(cfg.project(d)["advice"], True)

    def test_broken_file_raises_config_error(self):
        d = tracked_mkdtemp()
        Path(os.path.join(d, "pipeline.json")).write_text("{nope", encoding="utf-8")
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.project(d)
        self.assertIn(os.path.join(d, "pipeline.json"), str(ctx.exception))


class ProjectCacheTest(unittest.TestCase):
    def setUp(self):
        cfg.clear_cache()
        self.addCleanup(cfg.clear_cache)

    def test_same_file_is_read_once_and_copies_are_independent(self):
        d = tracked_mkdtemp()
        Path(d, "pipeline.json").write_text(json.dumps({"branch_release": "main", "test_env": {"X": "1"}}))
        first = cfg.project(d)
        first["test_env"]["X"] = "changed"
        self.assertEqual(cfg.project(d)["test_env"], {"X": "1"})
        self.assertEqual(len(cfg._PROJECT_CACHE), 1)

    def test_edited_pipeline_json_is_read_again(self):
        d = tracked_mkdtemp()
        p = Path(d, "pipeline.json")
        p.write_text(json.dumps({"branch_release": "main"}))
        self.assertEqual(cfg.project(d)["branch_release"], "main")
        p.write_text(json.dumps({"branch_release": "trunk-2"}))
        self.assertEqual(cfg.project(d)["branch_release"], "trunk-2")

    def test_clear_cache_forgets_results(self):
        d = tracked_mkdtemp()
        cfg.project(d)
        self.assertTrue(cfg._PROJECT_CACHE)
        cfg.clear_cache()
        self.assertFalse(cfg._PROJECT_CACHE)

    def test_git_derived_release_branch_follows_repo_in_process(self):
        # no clear_cache(): the git-detected release branch must track the repo HEAD, not the first answer
        d = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "trunk", d], check=True)
        self.assertEqual(cfg.project(d)["branch_release"], "trunk")
        subprocess.run(["git", "-C", d, "branch", "-m", "trunk", "develop"], check=True)
        self.assertEqual(cfg.project(d)["branch_release"], "develop")

    def test_pipeline_json_release_branch_stays_cached(self):
        d = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "trunk", d], check=True)
        Path(d, "pipeline.json").write_text(json.dumps({"branch_release": "main"}))
        self.assertEqual(cfg.project(d)["branch_release"], "main")
        self.assertEqual(len(cfg._PROJECT_CACHE), 1)
        subprocess.run(["git", "-C", d, "branch", "-m", "trunk", "develop"], check=True)
        self.assertEqual(cfg.project(d)["branch_release"], "main")

    def test_git_not_spawned_again_while_repo_state_is_unchanged(self):
        d = tracked_mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "trunk", d], check=True)
        with mock.patch.object(cfg.subprocess, "run", wraps=subprocess.run) as run:
            self.assertEqual(cfg.project(d)["branch_release"], "trunk")
            first = run.call_count
            for _ in range(3):
                self.assertEqual(cfg.project(d)["branch_release"], "trunk")
        self.assertGreater(first, 0)
        self.assertEqual(run.call_count, first)


class RepoPathTest(unittest.TestCase):
    def test_repo_key_joins_workspace(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "pipeline.json").write_text(json.dumps({"repo": "r", "branch_release": "main"}))
            self.assertEqual(cfg.repo_path(t), os.path.join(t, "r"))
            self.assertEqual(cfg.repo_dir(t, {}), t)


class WorkBranchTest(unittest.TestCase):
    def test_template_filled_with_feature_or_stage(self):
        c = {"branch_work": "stage/{feature}"}
        self.assertEqual(cfg.work_branch(c, "feat"), "stage/feat")
        self.assertEqual(cfg.work_branch(c, "feat/phases/01-a"), "stage/feat")
        self.assertEqual(cfg.work_branch(c, "s1"), "stage/s1")
        self.assertEqual(cfg.work_branch({}, "x"), "stage/x")

    def test_fixed_name_wins(self):
        self.assertEqual(cfg.work_branch({"branch_work": "dev"}, "feat"), "dev")


if __name__ == "__main__":
    unittest.main()
