"""worktree.link_private: nested repos without a worktree are linked to the main checkout (synthetic dirs)."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import worktree
from tests.support.tmpdirs import tracked_mkdtemp


def nested(repo, rel):
    os.makedirs(os.path.join(repo, rel, ".git"))


class NestedRepoDepth(unittest.TestCase):
    def test_a_repo_at_depth_three_is_found_and_none_below_it(self):
        repo = tracked_mkdtemp()
        for rel in ("p/q", "u/v/w", "u/v/w/x/y"):
            nested(repo, rel)
        os.makedirs(os.path.join(repo, "d1/d2/d3/d4/.git"))  # depth 4: out of reach
        self.assertEqual(sorted(worktree.nested_repos(repo)), ["p/q", "u/v/w"])


class LinkPrivate(unittest.TestCase):
    def setUp(self):
        self.repo, self.root = tracked_mkdtemp(), tracked_mkdtemp()
        subprocess.run(["git", "-C", self.root, "init", "-q"], check=True)
        with open(os.path.join(self.root, ".gitignore"), "w") as f:
            f.write("workspace/*\n")
        nested(self.repo, "workspace/a")
        nested(self.repo, "workspace/b")
        nested(self.repo, "other/x")

    def test_links_prefixed_nested_repos_except_the_skipped(self):
        made = worktree.link_private(self.repo, self.root, "workspace/", {"workspace/a"})
        self.assertEqual(made, [os.path.join(self.root, "workspace/b")])
        self.assertEqual(os.path.realpath(made[0]), os.path.realpath(os.path.join(self.repo, "workspace/b")))
        self.assertFalse(os.path.lexists(os.path.join(self.root, "workspace/a")))
        self.assertFalse(os.path.lexists(os.path.join(self.root, "other/x")))

    def test_a_link_git_does_not_ignore_is_removed_again(self):
        with open(os.path.join(self.root, ".gitignore"), "w") as f:
            f.write("")
        self.assertEqual(worktree.link_private(self.repo, self.root, "workspace/"), [])
        self.assertFalse(os.path.lexists(os.path.join(self.root, "workspace/a")))

    def test_off_without_a_prefix_and_never_replaces_an_existing_path(self):
        self.assertEqual(worktree.link_private(self.repo, self.root, ""), [])
        os.makedirs(os.path.join(self.root, "workspace/b"))
        self.assertEqual(worktree.link_private(self.repo, self.root, "workspace/"), [os.path.join(self.root, "workspace/a")])
        self.assertFalse(os.path.islink(os.path.join(self.root, "workspace/b")))


if __name__ == "__main__":
    unittest.main()
