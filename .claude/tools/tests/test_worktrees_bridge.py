"""pipeline/worktrees.py: the bridge to wt.py is off without `"worktrees": true`, reads the manifest, and never raises from job_diff."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import worktrees
from tests.support.tmpdirs import tracked_mkdtemp


def workspace(on=True, **extra):
    d = tracked_mkdtemp()
    if on:
        Path(d, "pipeline.json").write_text(json.dumps({"worktrees": True, **extra}))
    return d


class TestJobInfo(unittest.TestCase):
    def test_off_without_flag(self):
        a = SimpleNamespace(dir=workspace(on=False), stage="s")
        self.assertIsNone(worktrees.job_info(a, "alpha", create=True))

    def test_read_only_uses_manifest_and_needs_existing_root(self):
        d = workspace()
        a = SimpleNamespace(dir=d, stage="s")
        info = {"root": d, "branch": "job/alpha"}
        with mock.patch.object(worktrees.wt, "read_manifest", return_value={"alpha": info}):
            self.assertEqual(worktrees.job_info(a, "alpha", create=False), info)
            self.assertIsNone(worktrees.job_info(a, "beta", create=False))
        gone = {"root": d + "/missing"}
        with mock.patch.object(worktrees.wt, "read_manifest", return_value={"alpha": gone}):
            self.assertIsNone(worktrees.job_info(a, "alpha", create=False))

    def test_create_sets_base_from_config(self):
        a = SimpleNamespace(dir=workspace(branch_work="feat"), stage="s")
        with mock.patch.object(worktrees.wt, "create", return_value={"root": "/x"}) as c:
            info = worktrees.job_info(a, "alpha", create=True)
        c.assert_called_once_with(a.dir, "s", "alpha")
        assert info is not None
        self.assertEqual(info["base"], "feat")

    def test_create_default_base_is_stage_branch(self):
        a = SimpleNamespace(dir=workspace(), stage="s")
        with mock.patch.object(worktrees.wt, "create", return_value={"root": "/x"}):
            info = worktrees.job_info(a, "alpha", create=True)
        assert info is not None
        self.assertEqual(info["base"], "stage/s")


class TestJobDiff(unittest.TestCase):
    def test_empty_when_no_job(self):
        a = SimpleNamespace(dir=workspace(on=False), stage="s")
        self.assertEqual(worktrees.job_diff(a, "alpha"), "")

    def test_returns_diff_and_swallows_errors(self):
        a = SimpleNamespace(dir="d", stage="s")
        with mock.patch.object(worktrees, "job_info", return_value={"root": "/x"}):
            with mock.patch.object(worktrees.wt, "diff", return_value="DIFF") as df:
                self.assertEqual(worktrees.job_diff(a, "alpha", limit=5), "DIFF")
                df.assert_called_once_with("d", "s", "alpha", 5)
            for exc in (RuntimeError("x"), OSError("y")):
                with mock.patch.object(worktrees.wt, "diff", side_effect=exc):
                    self.assertEqual(worktrees.job_diff(a, "alpha"), "")

    def test_drops_generated_docs_index_from_review_diff(self):
        d = workspace()
        a = SimpleNamespace(dir=d, stage="s")
        text = "=== repo . (w, branch b) ===\n src/a.py | 1 +\n docs/INDEX.md | 2 +-\n 2 files changed\n\ndiff --git a/src/a.py b/src/a.py\n+x\ndiff --git a/docs/INDEX.md b/docs/INDEX.md\n+idx\n"
        with mock.patch.object(worktrees, "job_info", return_value={"root": "/x"}), mock.patch.object(worktrees.wt, "diff", return_value=text):
            out = worktrees.job_diff(a, "alpha")
        self.assertIn("src/a.py", out)
        self.assertIn("+x", out)
        self.assertNotIn("INDEX.md", out)
        self.assertNotIn("+idx", out)

    def test_keeps_index_files_outside_docs_dir(self):
        text = (
            " docs/guide.md | 1 +\n other/INDEX.md | 1 +\n\ndiff --git a/docs/guide.md b/docs/guide.md\n+g\ndiff --git a/other/INDEX.md b/other/INDEX.md\n+o\n"
        )
        self.assertEqual(worktrees.drop_generated_index(text, "docs"), text)

    def test_config_error_gives_empty_diff_not_a_crash(self):
        a = SimpleNamespace(dir=workspace(), stage="s")
        with (
            mock.patch.object(worktrees, "job_info", return_value={"root": "/x"}),
            mock.patch.object(worktrees.wt, "diff", return_value="DIFF"),
            mock.patch.object(worktrees, "project", side_effect=worktrees.ConfigError("bad gates")),
        ):
            self.assertEqual(worktrees.job_diff(a, "alpha"), "")

    def test_docs_dir_setting_names_the_index(self):
        text = " site/docs/INDEX.md | 1 +\n\ndiff --git a/site/docs/INDEX.md b/site/docs/INDEX.md\n+i\n"
        self.assertEqual(worktrees.drop_generated_index(text, "/site/docs/").strip(), "")


if __name__ == "__main__":
    unittest.main()
