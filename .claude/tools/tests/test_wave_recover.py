import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave
from pipeline.configuration import EV_DISPATCH, PGID_FILE_NAME, STALE_RERUN_MAX
from pipeline.ctx import Ctx
from pipeline.util import append_event


class RecoverUnreportedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wave-recover-")
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        self.c = Ctx(SimpleNamespace(stage="s", dir=self.d))
        os.makedirs(self.c.w, exist_ok=True)

    def dispatch(self, i, task="task-1", role="dev"):
        append_event(self.c.log, {"ev": EV_DISPATCH, "id": f"d{i}", "task": task, "role": role})

    def test_rerun_limit_comes_from_the_setting(self):
        # the setting allows STALE_RERUN_MAX re-runs of one task and role; the next dispatch that dies is the lead's call
        self.dispatch(1)
        for i in range(1, STALE_RERUN_MAX + 1):
            self.assertEqual(cli_wave.recover_unreported(self.c), 1, f"re-run {i} of {STALE_RERUN_MAX}")
            self.dispatch(i + 1)
        self.assertEqual(cli_wave.recover_unreported(self.c), 0)

    def test_other_role_has_its_own_rerun(self):
        self.dispatch(1, role="dev")
        self.assertEqual(cli_wave.recover_unreported(self.c), 1)
        self.dispatch(2, role="reviewer")
        self.assertEqual(cli_wave.recover_unreported(self.c), 1)


class KillStaleSessionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wave-pgid-")
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        self.c = Ctx(SimpleNamespace(stage="s", dir=self.d))
        os.makedirs(self.c.w, exist_ok=True)

    def spawn_session(self):
        """A real sleeping child in its own process group, as a session of the dead driver."""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def test_recover_kills_the_live_group_listed_in_the_pgid_file(self):
        p = self.spawn_session()
        with open(os.path.join(self.c.w, PGID_FILE_NAME), "w", encoding="utf-8") as f:
            f.write(f"{p.pid}\n")
        append_event(self.c.log, {"ev": EV_DISPATCH, "id": "d1", "task": "task-1", "role": "dev"})
        self.assertEqual(cli_wave.recover_unreported(self.c), 1)
        self.assertEqual(p.wait(timeout=30), -9)  # SIGKILL: the session is gone before the re-run starts
        self.assertFalse(os.path.exists(os.path.join(self.c.w, PGID_FILE_NAME)))

    def test_no_unreported_dispatch_leaves_the_group_alone(self):
        p = self.spawn_session()
        with open(os.path.join(self.c.w, PGID_FILE_NAME), "w", encoding="utf-8") as f:
            f.write(f"{p.pid}\n")
        self.assertEqual(cli_wave.recover_unreported(self.c), 0)
        self.assertIsNone(p.poll())


if __name__ == "__main__":
    unittest.main()
