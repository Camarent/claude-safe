import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import util, worktree
from pipeline.configuration import EV_SETUP, METRICS_LOG, WORK_DIR
from tests.support.tmpdirs import tracked_tmpdir

SUBJECT = "feat(docs): regenerate index"
SETUP_FOR = "setup-probe"  # task name the setup events are tagged with


def git(d, *args):
    return subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, check=False)


class RegenIndexByWaveTest(unittest.TestCase):
    """commit() regenerates the docs index only for a task that runs alone in its wave (a_dir holds the briefs; the job worktree is a temp repo)."""

    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        git(self.d, "init")
        git(self.d, "config", "user.name", "T")
        git(self.d, "config", "user.email", "t@example.com")
        git(self.d, "config", "commit.gpgsign", "false")
        Path(self.d, "pipeline.json").write_text('{"docs_dir": "docs"}')
        Path(self.d, "docs").mkdir()
        Path(self.d, "docs", "modules.txt").write_text("")
        Path(self.d, "f.txt").write_text("x")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "init")
        Path(self.d, "docs", "INDEX.md").write_text("stale\n")
        Path(self.d, "f.txt").write_text("changed")
        # the briefs live in a separate main-checkout dir, so their folder never lands in the job commit
        self.main = tracked_tmpdir()
        self.a_dir = self.main.name
        self.plan = Path(self.a_dir, WORK_DIR, "s")
        self.plan.mkdir(parents=True)
        patch = mock.patch.object(worktree, "_parts", return_value=(None, "job/x", [("", self.d, self.d)]))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()
        self.main.cleanup()

    def brief(self, task, wave):
        Path(self.plan, f"{task}.md").write_text(f"# {task}\n\nWave: {wave}\n")

    def index_text(self):
        return Path(self.d, "docs", "INDEX.md").read_text()

    def test_parallel_wave_keeps_stale_index(self):
        self.brief("task-1", "W1")
        self.brief("task-2", "W1")
        self.assertIsNotNone(worktree.commit(self.a_dir, "s", "task-1", SUBJECT)["."])
        self.assertIn("stale", self.index_text())

    def test_single_task_wave_regenerates_index(self):
        self.brief("task-1", "W1")
        self.brief("task-2", "W2")
        self.assertIsNotNone(worktree.commit(self.a_dir, "s", "task-1", SUBJECT)["."])
        self.assertIn("# Index", self.index_text())
        self.assertNotIn("stale", self.index_text())


class SetupEventTest(unittest.TestCase):
    """_run_setup logs one EV_SETUP metrics line per command with its exit code and seconds, read from an injected clock."""

    def setUp(self):
        self.main = tracked_tmpdir()
        self.a_dir = self.main.name
        self.root = tracked_tmpdir()
        self.addCleanup(self.root.cleanup)
        self.addCleanup(self.main.cleanup)

    def events(self):
        return [r for r in util.read_jsonl(os.path.join(self.a_dir, WORK_DIR, "s", METRICS_LOG)) if r.get("ev") == EV_SETUP]

    def test_each_command_logs_seconds(self):
        ticks = iter([0.0, 1.5, 1.5, 4.0])  # start/end of command 1, start/end of command 2
        cfg = {"worktree_setup": ["true", "true"], "worktree_setup_timeout": 30}
        worktree._run_setup(self.a_dir, "s", SETUP_FOR, self.root.name, cfg, clock=lambda: next(ticks))
        evs = self.events()
        self.assertEqual([e["seconds"] for e in evs], [1.5, 2.5])
        self.assertEqual([e["rc"] for e in evs], [0, 0])
        self.assertEqual({e["for"] for e in evs}, {SETUP_FOR})
        self.assertNotIn("task", evs[0])

    def test_failing_command_is_logged_then_raises(self):
        ticks = iter([0.0, 0.5])
        cfg = {"worktree_setup": ["exit 3"], "worktree_setup_timeout": 30}
        with mock.patch.object(worktree, "remove") as remove, self.assertRaises(RuntimeError):
            worktree._run_setup(self.a_dir, "s", SETUP_FOR, self.root.name, cfg, clock=lambda: next(ticks))
        remove.assert_called_once()
        self.assertEqual([(e["rc"], e["seconds"]) for e in self.events()], [(3, 0.5)])


if __name__ == "__main__":
    unittest.main()
