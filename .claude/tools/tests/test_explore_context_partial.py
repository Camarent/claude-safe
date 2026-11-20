"""explore_context: an empty answer is treated as a failure (fallback runs); a cut-off loop is labelled PARTIAL. No lp, no model."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "localmodel"))
from localmodel import explore as ex
from localmodel import plan_context as pc


class ExploreContextTest(unittest.TestCase):
    def run_ctx(self, files, ended):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        got = [(os.path.basename(root), p, "why", "code") for p in files] if files is not None else None
        with open(os.path.join(root, "a.py"), "w") as f:
            f.write("x = 1\n")
        ex.agent_files.STATS["ended"] = ended
        with (
            mock.patch.object(pc, "pick_roots", return_value=[root]),
            mock.patch.object(ex, "explore", return_value=got),
            mock.patch.object(pc, "seed_leads", return_value=""),
        ):
            return pc.explore_context(root, "req", {}), os.path.basename(root)

    def test_empty_answer_is_none_and_marked(self):
        out, _ = self.run_ctx([], "answer")
        self.assertIsNone(out)
        self.assertEqual(ex.agent_files.STATS["ended"], "empty")

    def test_cut_off_loop_is_labelled_partial(self):
        out, _ = self.run_ctx(["a.py"], "budget")
        self.assertIn("PARTIAL", "" if out is None else out)

    def test_finished_loop_has_no_partial_label(self):
        out, _ = self.run_ctx(["a.py"], "answer")
        self.assertIn("a.py", out)
        self.assertNotIn("PARTIAL", out)

    def test_explore_log_is_written_when_a_path_is_set(self):
        logdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, logdir, ignore_errors=True)
        path = os.path.join(logdir, "x.md.explore.log")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        def fake(repos, text, log=None, leads=""):
            log.append("turn 1: find {} -> 3 chars: 'abc'")
            return [(os.path.basename(root), "a.py", "why", "code")]

        with (
            mock.patch.object(pc, "pick_roots", return_value=[root]),
            mock.patch.object(ex, "explore", fake),
            mock.patch.object(pc, "seed_leads", return_value=""),
        ):
            pc.explore_context(root, "req", {}, path)
        body = open(path, encoding="utf-8").read()
        self.assertIn("turn 1: find", body)
        self.assertIn("code: ", body)
        self.assertIn("stats: ", body)

    def test_seed_leads_from_find_and_log(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)
        for n in ("a.py", "b.py", "c.py"):
            with open(os.path.join(root, n), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
        subprocess.run(["git", "-C", root, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "m"], check=True, capture_output=True)
        with mock.patch.object(pc, "lp", return_value="c.py:1  hit\n"):
            got = pc.seed_leads({"r": root}, "change a.py please")
        self.assertIn("lp find: c.py", got)
        self.assertIn("named in the request: a.py", got)
        self.assertIn("(git log): b.py, c.py", got)
        with mock.patch.object(pc, "lp", return_value=""):
            self.assertEqual(pc.seed_leads({"r": root}, "nothing named"), "")

    def _repo(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)
        for n in ("a.py", "b.py"):
            with open(os.path.join(root, n), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
        subprocess.run(["git", "-C", root, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "m"], check=True, capture_output=True)
        return root

    def test_seed_leads_one_git_log_call(self):
        root = self._repo()
        calls = []
        real = pc.gitx.run

        def spy(repo, *args, **kw):
            calls.append(list(args))
            return real(repo, *args, **kw)

        with mock.patch.object(pc, "lp", return_value=""), mock.patch.object(pc.gitx, "run", spy):
            pc.seed_leads({"r": root}, "change a.py and b.py")
        self.assertEqual(len(calls), 2)  # one git log over the named paths, one git show over its commits
        self.assertEqual(calls[0][-3:], ["--", "a.py", "b.py"])

    def test_seed_leads_strips_leading_dash_of_find_query(self):
        root = self._repo()
        with mock.patch.object(pc, "lp", return_value="") as lp:
            pc.seed_leads({"r": root}, "--no-model fix")
        self.assertEqual(lp.call_args.args[2], "no-model fix")

    def test_seed_leads_stops_at_deadline(self):
        root = self._repo()
        with mock.patch.object(pc, "lp", return_value="c.py:1 x\n") as lp, mock.patch.object(pc, "SEED_DEADLINE_S", 0):
            self.assertEqual(pc.seed_leads({"r": root}, "a.py"), "")
        lp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
