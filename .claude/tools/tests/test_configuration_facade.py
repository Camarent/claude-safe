"""pipeline.configuration is a package; its facade must keep every key name importable (callers import them by name)."""

import importlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.tmpdirs import tracked_mkdtemp

KEY_NAMES = (
    "HOME",
    "TOOLS_DIR",
    "AGENTS_DIR",
    "PRACTICES_DIR",
    "DEFAULT_FLAGS",
    "BASE_FLAGS",
    "BASH_ALLOW",
    "OPS_FLAGS",
    "PLAIN_SHELL_TOOLS",
    "GIT_SAFE_ENV",
    "GIT_SAFE_DIR_ARGS",
    "PUBLISH_RE",
    "PUBLISH_LINE_RE",
    "ADD_DIR_AGENTS",
    "ADD_DIR_FLAG",
    "ENV_REVIEW_DIR",
    "ENV_TIMEOUT",
    "ROLES",
    "AUDITORS",
    "PROJECT_DEFAULTS",
    "QUALITY_TOOLS",
    "MAX_TRIES",
    "MAX_ROUNDS",
    "WORK_DIR",
    "TASKS_FILE",
    "SESSION_TIMEOUT_S",
    "TASK_FILES_FAIL",
    "DEFAULT_WORKSPACE",
    "ConfigError",
    "_PROJECT_CACHE",
    "GIT_DIRS_CACHE",
    "project",
    "repo_dir",
    "repo_dirs",
    "repo_path",
    "publish_entries",
    "claude_flags",
    "release_branch",
    "work_branch",
    "test_env",
    "gates_of",
    "quality_tools",
    "clear_cache",
    "tool_cmd",
    "cs_names",
    "allowed",
    "_check_publish_entry",
    "_escapes_project",
    "_git_out",
)


class ConfigurationFacadeTest(unittest.TestCase):
    def test_key_names_importable(self):
        c = importlib.import_module("pipeline.configuration")
        self.assertEqual([n for n in KEY_NAMES if not hasattr(c, n)], [])

    def test_default_flags_hold_bash_entries(self):
        c = importlib.import_module("pipeline.configuration")
        self.assertIn("'Bash(", c.DEFAULT_FLAGS)
        self.assertTrue(c.BASH_ALLOW)

    def test_home_is_the_pipeline_folder(self):
        c = importlib.import_module("pipeline.configuration")
        self.assertEqual(c.TOOLS_DIR, c.HOME + "/tools")

    def test_removed_review_ccn_key_in_pipeline_json_does_not_raise(self):
        # the review_ccn route is gone; an old pipeline.json that still sets the key must load (unknown keys are kept, none raise)
        c = importlib.import_module("pipeline.configuration")
        d = tracked_mkdtemp()
        with open(os.path.join(d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"review_ccn": 7}, f)
        self.assertEqual(c.project(d)["review_ccn"], 7)


if __name__ == "__main__":
    unittest.main()
