"""wt.py merge: a Conflict names the nested repos already merged in the same run (they stay merged); a conflict in the first repo names none."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402

T1 = "task-1"
NESTED = "workspace/c"


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class MergePartialConflict(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(f"{self.m}/{NESTED}")
        os.makedirs(f"{self.t}/.work/s")
        json.dump({"repo": "main", "branch_work": "dev", "nested_repo_dir_prefix": "workspace/"}, open(f"{self.t}/pipeline.json", "w"))
        for r in (self.m, f"{self.m}/{NESTED}"):
            sh("-C", r, "init", "-q", "-b", "dev")
            open(f"{r}/f", "w").write("x")
            sh("-C", r, "add", "f")
            sh("-C", r, "commit", "-qm", "i")
        open(f"{self.t}/.work/s/{T1}.md", "w").write(f"Predicted paths: {NESTED}/f f")
        self.r = wt.create(self.t, "s", T1)

    def edit_job(self, nested_name, main_text):
        """The job edits a file in the nested repo (no conflict) and f in the main repo (conflicts once main moves)."""
        open(f"{self.r['repos'][NESTED]}/{nested_name}", "w").write("job")
        open(f"{self.r['root']}/f", "w").write("job")
        wt.commit(self.t, "s", T1, "feat(x): job side")
        open(f"{self.m}/f", "w").write(main_text)
        sh("-C", self.m, "commit", "-qam", "main side")

    def test_conflict_in_main_names_the_nested_repo_already_merged(self):
        self.edit_job("g", "main")
        with self.assertRaises(wt.Conflict) as ctx:
            wt.merge(self.t, "s", T1)
        self.assertIn(f"already merged, kept: {NESTED}", str(ctx.exception))
        self.assertTrue(os.path.exists(f"{self.m}/{NESTED}/g"))  # the nested merge stayed

    def test_nested_repo_without_job_commits_is_not_named(self):
        """The job edits only main f; the nested repo has nothing to merge ("nothing"), so a main conflict names no repo."""
        open(f"{self.r['root']}/f", "w").write("job")
        wt.commit(self.t, "s", T1, "feat(x): job side")
        open(f"{self.m}/f", "w").write("main")
        sh("-C", self.m, "commit", "-qam", "main side")
        with self.assertRaises(wt.Conflict) as ctx:
            wt.merge(self.t, "s", T1)
        self.assertNotIn("already merged", str(ctx.exception))
        self.assertFalse(os.path.exists(f"{self.m}/{NESTED}/g"))  # nothing was merged into the nested repo

    def test_conflict_in_first_repo_names_no_merged_repo(self):
        self.edit_job("g", "main")
        open(f"{self.m}/{NESTED}/g", "w").write("main")  # the nested main moves too: the nested merge conflicts first
        sh("-C", f"{self.m}/{NESTED}", "add", "g")
        sh("-C", f"{self.m}/{NESTED}", "commit", "-qm", "nested main side conflict")
        with self.assertRaises(wt.Conflict) as ctx:
            wt.merge(self.t, "s", T1)
        self.assertNotIn("already merged", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
