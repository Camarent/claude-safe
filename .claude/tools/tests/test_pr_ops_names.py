"""pipeline.pr_ops keeps every module-level name it had before the release split (names frozen at HEAD), and both split modules import in a fresh interpreter."""

import os
import subprocess
import sys
import unittest

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS_DIR)
from pipeline import pr_ops  # noqa: E402

# module-level bindings of pipeline/pr_ops.py at HEAD (before the split): defs, classes, constants and imports.
# tomllib, PLAN_FILE, PROJECT_DEFAULTS and section_body were imports only used by the release block: they moved to pr_release.py.
HEAD_NAMES = [
    "json",
    "os",
    "re",
    "subprocess",
    "sys",
    "time",
    "Path",
    "ci_clean",
    "cleanup",
    "gitx",
    "pr_mode",
    "webskip",
    "worktree",
    "ci_commands",
    "ignore_hint",
    "BLOCKED",
    "CHECKS_TIMEOUT_S",
    "CI_DENIED_MSG",
    "CI_EMPTY_POLLS",
    "CI_POLL_S",
    "CI_RUNS_LIMIT",
    "DONE",
    "GH_CHECKS_PENDING_RC",
    "GIT_LONG_TIMEOUT_S",
    "GIT_NETWORK_TIMEOUT_S",
    "GIT_SAFE_ENV",
    "PUSH_ERR_HEAD",
    "PUSH_ERR_TAIL",
    "TASKS_FILE",
    "WORK_DIR",
    "all_gates_of",
    "project",
    "release_branch",
    "repo_path",
    "ENV_OUTPUT_RE",
    "PR_GATE_TIMEOUT_S",
    "PR_GATES_STEP",
    "Run",
    "TOOLS",
    "checks_timeout",
    "Blocked",
    "_run",
    "_git",
    "_has_origin",
    "_ends",
    "_pushed",
    "_pr_title",
    "_open_pr",
    "_view",
    "_landed",
    "CiReadError",
    "_ci_runs",
    "_ci_wait",
    "_ci_failed",
    "_pr_checks",
    "_failing",
    "web_note",
    "append_body",
    "_gates_gate",
    "_ready",
    "TITLE_RE",
    "_idle",
    "_empty",
    "_nested_unlanded",
    "_merge_title",
    "_squash_forbidden",
    "SystemClock",
    "_say",
    "_poll",
    "_checks_read",
    "_mergeable_read",
    "_checks_gate",
    "_merge",
    "LEVELS",
    "_release_level",
    "_goal_version",
    "_ver",
    "_PIN",
    "_uses_workspace_version",
    "_rewrite_pins",
    "_bump",
    "_hook",
    "_plan_bump",
    "_release_ref",
    "_version_bump",
    "_SEMVER",
    "_already_released",
    "_merged_sha",
    "_ensure_tag",
    "_github_release",
    "_release",
    "_cleanup",
    "_to_release_branch",
    "_prune_origin",
    "_clean_repo",
    "_squash_commit",
    "_back",
    "_local_merge_repo",
    "_local_merge",
    "_green",
    "_red",
    "_merge_green",
    "_sweep_ff",
    "_sweep",
    "_no_push_note",
    "_pr_step",
    "run",
    "sweep_before_branch",
]


def _fresh_import(first):
    """Import `first` in a new interpreter (nothing cached yet) and return its exit code and stderr."""
    code = f"import sys; sys.path.insert(0, {TOOLS_DIR!r}); import pipeline.{first}"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    return r.returncode, r.stderr


class PrOpsNames(unittest.TestCase):
    def test_every_head_name_is_importable_from_pr_ops(self):
        missing = [n for n in HEAD_NAMES if not hasattr(pr_ops, n)]
        self.assertEqual(missing, [])

    def test_release_module_imports_first(self):
        rc, err = _fresh_import("pr_release")
        self.assertEqual(rc, 0, err)

    def test_pr_ops_imports_first(self):
        rc, err = _fresh_import("pr_ops")
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
