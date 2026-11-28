"""Gate-run record: tree hash of a working tree, the run log in the git common dir, and the find_passed lookup."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOOLS)
from pipeline import gate_record as G  # noqa: E402
from pipeline.configuration import GATE_KIND_FLAKE  # noqa: E402  (the sys.path line above must run first)

IDENT = ("-c", "user.name=t", "-c", "user.email=t@t")


def git(cwd, *args):
    return subprocess.run(["git", *IDENT, "-C", cwd, *args], capture_output=True, text=True, check=True).stdout


def make_repo(root, files):
    """git init in a new dir root with files committed (one commit) -> root."""
    os.makedirs(root)
    git(root, "init", "-q")
    for name, text in files.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            f.write(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class TestTreeHash(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def test_tree_changes_with_untracked_and_tracked_edits(self):
        repo = make_repo(os.path.join(self.root, "a"), {"x.txt": "1"})
        before = G.tree_hash(repo)
        with open(os.path.join(repo, "new.txt"), "w", encoding="utf-8") as f:
            f.write("n")
        with_untracked = G.tree_hash(repo)
        self.assertNotEqual(before, with_untracked)
        with open(os.path.join(repo, "x.txt"), "w", encoding="utf-8") as f:
            f.write("2")
        self.assertNotEqual(with_untracked, G.tree_hash(repo))

    def test_same_content_same_tree(self):
        one = make_repo(os.path.join(self.root, "b"), {"x.txt": "same"})
        two = make_repo(os.path.join(self.root, "c"), {"x.txt": "same"})
        self.assertEqual(G.tree_hash(one), G.tree_hash(two))

    def test_real_index_untouched(self):
        repo = make_repo(os.path.join(self.root, "d"), {"x.txt": "1"})
        with open(os.path.join(repo, "new.txt"), "w", encoding="utf-8") as f:
            f.write("n")
        status_before = git(repo, "status", "--porcelain")
        G.tree_hash(repo)
        self.assertEqual(git(repo, "status", "--porcelain"), status_before)
        self.assertNotIn("new.txt", git(repo, "ls-files"))

    def test_non_git_dir_is_none(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        # the scratch TMPDIR may sit inside a repo: the ceiling stops git from finding that repo's .git
        with unittest.mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": self.root}):
            self.assertIsNone(G.tree_hash(plain))
            self.assertIsNone(G.find_passed(plain, ["x"]))
            G.record(plain, ["x"], 0)
        self.assertEqual(os.listdir(plain), [])


class TestRecord(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = make_repo(os.path.join(tmp.name, "r"), {"x.txt": "1"})

    def test_cmd_key_strips_launcher_and_separator(self):
        self.assertEqual(G.cmd_key(["cs-qrun", "--", "scripts/dev.sh", "test"]), ["scripts/dev.sh", "test"])
        self.assertEqual(G.cmd_key(["/x/qrun.py", "--", "a"]), ["a"])
        self.assertEqual(G.cmd_key(["a", "--", "b"]), ["a", "--", "b"])

    def test_green_run_found_red_run_never(self):
        G.record(self.repo, ["cs-qrun", "--", "t"], 1)
        self.assertIsNone(G.find_passed(self.repo, ["t"]))
        G.record(self.repo, ["cs-qrun", "--", "t"], 0)
        rec = G.find_passed(self.repo, ["t"])
        assert rec is not None  # narrows Optional for the type checker; the failure message is the assert itself
        self.assertEqual(rec["rc"], 0)
        self.assertEqual(rec["cmd"], ["t"])
        self.assertEqual(rec["tree"], G.tree_hash(self.repo))

    def test_other_command_or_tree_or_base_not_found(self):
        G.record(self.repo, ["t"], 0)
        self.assertIsNone(G.find_passed(self.repo, ["other"]))
        self.assertIsNone(G.find_passed(self.repo, ["t"], tree="0" * 40))
        self.assertIsNone(G.find_passed(self.repo, ["t"], base="0" * 40))
        found = G.find_passed(self.repo, ["t"])
        assert found is not None  # narrows Optional for the type checker
        self.assertIsNotNone(G.find_passed(self.repo, ["t"], base=found["base"]))

    def test_log_in_common_dir_one_json_line_per_run(self):
        G.record(self.repo, ["t"], 0, now=1.0)
        G.record(self.repo, ["t"], 2, now=2.0)
        log = os.path.join(self.repo, ".git", G.RECORD_FILE)
        with open(log, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f.read().splitlines()]
        self.assertEqual([r["rc"] for r in rows], [0, 2])
        self.assertEqual([r["ts"] for r in rows], [1.0, 2.0])
        self.assertEqual(len({r["run"] for r in rows}), 2)
        self.assertEqual(set(rows[0]), {"run", "cmd", "tree", "base", "rc", "ts", "secs", "level", "kind"})

    def test_secs_and_level_written_and_optional(self):
        G.record(self.repo, ["t"], 0, now=1.0, secs=2.5, level="parallel")
        G.record(self.repo, ["t"], 0, now=2.0)  # old caller: no secs or level given
        log = os.path.join(self.repo, ".git", G.RECORD_FILE)
        rows = G._entries(log)
        self.assertEqual((rows[0]["secs"], rows[0]["level"]), (2.5, "parallel"))
        self.assertEqual((rows[1]["secs"], rows[1]["level"]), (None, None))
        self.assertIsNotNone(G.find_passed(self.repo, ["t"], base=rows[1]["base"]))

    def test_flake_record_counted_and_never_a_pass(self):
        G.record(self.repo, ["t"], 0, kind=GATE_KIND_FLAKE)
        self.assertEqual(G.flake_count(self.repo), 1)
        self.assertIsNone(G.find_passed(self.repo, ["t"]))
        G.record(self.repo, ["t"], 0)
        self.assertEqual(G.flake_count(self.repo), 1)
        found = G.find_passed(self.repo, ["t"])
        assert found is not None  # narrows Optional for the type checker
        self.assertIsNone(found["kind"])


if __name__ == "__main__":
    unittest.main()
