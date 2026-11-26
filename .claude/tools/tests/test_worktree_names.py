import os
import sys
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import worktree  # noqa: E402  (sys.path set above)

# every top-level name of pipeline/worktree.py before the split: the split must keep each one importable from pipeline.worktree
NAMES = [
    "SKIP",
    "LINK",
    "git",
    "_exclude_link",
    "_looks_like_repo",
    "_stray_ignored",
    "nested_repos",
    "nested_repo_of",
    "_base_branch",
    "_catch_up",
    "_add",
    "_job_worktrees",
    "RefreshConflict",
    "refresh",
    "refresh_stale",
    "layout",
    "_unique_branch",
    "_manifest",
    "read_manifest",
    "_manifest_lock",
    "live_job",
    "_write_manifest",
    "_seed_index",
    "_seed_build",
    "_copy_mtimes",
    "_run_setup",
    "repair_env",
    "link_private",
    "_check_named_dirs",
    "_put_entry",
    "_early_entry",
    "_drop_entry",
    "_add_with_entry",
    "_add_nested",
    "add_repo",
    "_wants_nested",
    "create",
    "_GIT_VERSION",
    "_git_version",
    "_need_merge_tree",
    "_unmerged",
    "skip_blocker",
    "empty_job",
    "settled_job",
    "remove",
    "_log_forced",
    "_refuse_unmerged_removal",
    "_remove_worktrees",
    "_remove_state_files",
    "Conflict",
    "Blocked",
    "SUBJECT",
    "subject_ok",
    "_parts",
    "_dirty",
    "PIPELINE_DIRS",
    "touched_nested",
    "dirty_report",
    "_regen_docs_index",
    "_apply_modes",
    "_parallel_wave",
    "commit",
    "PATCH_CAP",
    "_save_patch",
    "_abs_tails",
    "_predicted",
    "_record_scope",
    "CONFLICT_HUNK_CHARS",
    "_conflict_hunks",
    "merge",
    "_driver_holder",
    "_check_merge_preconditions",
    "_commit_squash",
    "MERGED_MARKER",
    "MERGED_MIN_SHA",
    "MERGED_LEGACY",
    "_job_heads",
    "_same_head",
    "marker_state",
    "marked_merged",
    "mark_merged",
    "is_merged",
    "diff",
    "clean",
    "review_marker",
    "review_begin",
    "review_reset",
    "review_pending",
    "list_wt",
]


class WorktreeNamesTest(unittest.TestCase):
    def test_every_old_name_importable_from_worktree(self):
        missing = [n for n in NAMES if not hasattr(worktree, n)]
        self.assertEqual(missing, [])

    def test_name_list_is_complete(self):
        self.assertEqual(len(NAMES), len(set(NAMES)))
        # 89 = top-level defs, classes and assigns of pipeline/worktree.py at 37c3488 (pre-split), checked by AST
        self.assertEqual(len(NAMES), 89)


if __name__ == "__main__":
    unittest.main()
