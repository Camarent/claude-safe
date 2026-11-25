"""cs-qrun --postgres: qrun starts a scratch instance from pipeline.json, sets its URL in the command env and removes the instance when the command ends. The start command is a fake script; no real Postgres."""

import contextlib
import io
import json
import os
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import qrun
from pipeline.configuration import ENV_QRUN_NO_RECORD
from tests.support.tmpdirs import tracked_mkdtemp

# fake start command: records its instance dir in argv[1]/inst, prints a URL, leaves a stop marker in the instance dir
FAKE_START = """import os, sys
inst = os.environ["QRUN_PG_DIR"]
open(os.path.join(sys.argv[1], "inst"), "w").write(inst)
open(os.path.join(inst, "running"), "w").write("1")
print("postgresql://fake/instance")
"""

CMD_READS_URL = [sys.executable, "-c", "import os; print('URL=' + os.environ['PGURL_TEST'])"]


class PostgresFlagTest(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tracked_mkdtemp())
        self.log = os.path.join(self.d, "q.log")
        self.script = os.path.join(self.d, "fake_pg.py")
        Path(self.script).write_text(FAKE_START, encoding="utf-8")
        self.write_cfg(qrun_pg_var="PGURL_TEST", qrun_pg_start=[sys.executable, "-I", self.script, self.d])
        old = os.getcwd()
        os.chdir(self.d)
        self.addCleanup(os.chdir, old)
        env = unittest.mock.patch.dict(os.environ, {ENV_QRUN_NO_RECORD: "1"})
        env.start()
        self.addCleanup(env.stop)

    def write_cfg(self, **keys):
        Path(self.d, "pipeline.json").write_text(json.dumps(keys), encoding="utf-8")

    def run_q(self, cmd, extra=("--postgres",)):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = qrun.main(["--log", self.log, *extra, "--", *cmd])
        return rc, buf.getvalue().splitlines(), err.getvalue()

    def test_url_reaches_command_and_instance_removed(self):
        rc, out, _ = self.run_q(CMD_READS_URL)
        self.assertEqual(rc, 0, out)
        self.assertIn("URL=postgresql://fake/instance", Path(self.log).read_text(encoding="utf-8"))
        inst = Path(self.d, "inst").read_text(encoding="utf-8")
        self.assertTrue(os.path.basename(inst).startswith(qrun.QRUN_TMP_PREFIX), inst)
        self.assertFalse(os.path.exists(inst), "instance dir must be removed when the command ends")

    def test_instance_removed_when_command_fails(self):
        rc, _, _ = self.run_q([sys.executable, "-c", "import sys; sys.exit(1)"])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(Path(self.d, "inst").read_text(encoding="utf-8")))

    def test_instance_removed_when_command_times_out(self):
        with unittest.mock.patch.object(qrun, "_timeout", return_value=2.0):
            rc, _, _ = self.run_q([sys.executable, "-c", "import time; time.sleep(60)"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(Path(self.d, "inst").read_text(encoding="utf-8")), "instance dir must be removed on command timeout")

    def test_no_flag_no_instance(self):
        rc, _, _ = self.run_q([sys.executable, "-c", "print('plain')"], extra=())
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "inst")), "the start command must not run without --postgres")

    def test_missing_key_is_exit_2(self):
        self.write_cfg(qrun_pg_var="PGURL_TEST")
        rc, _, err = self.run_q(CMD_READS_URL)
        self.assertEqual(rc, 2)
        self.assertIn("qrun_pg_start", err)
        self.assertFalse(os.path.exists(self.log), "the command must not run without a database")

    def test_start_timeout_kills_child_and_removes_instance(self):
        hang = (
            "import os, subprocess, sys, time\n"
            "inst = os.environ['QRUN_PG_DIR']\n"
            "open(os.path.join(sys.argv[1], 'inst'), 'w').write(inst)\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(os.path.join(sys.argv[1], 'pid'), 'w').write(str(c.pid))\n"
            "time.sleep(60)\n"
        )
        Path(self.script).write_text(hang, encoding="utf-8")
        with unittest.mock.patch.object(qrun, "_timeout", return_value=1.0):
            rc, _, err = self.run_q(CMD_READS_URL)
        self.assertEqual(rc, 2)
        self.assertIn("timeout", err)
        self.assertFalse(os.path.exists(Path(self.d, "inst").read_text(encoding="utf-8")), "instance dir must be removed on timeout")
        pid = int(Path(self.d, "pid").read_text(encoding="utf-8"))
        for _ in range(50):
            try:
                os.kill(pid, 0)
                # the test process is the subreaper here: a killed child stays a zombie until reaped
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    break
            except (ProcessLookupError, ChildProcessError):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the start command's child must be killed on timeout")

    def test_daemonized_server_lives_during_command_and_dies_after(self):
        # the start command leaves a detached child (a daemonized server) and exits: qrun must keep it for the command, kill it after
        daemon = (
            "import os, subprocess, sys\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "open(os.path.join(sys.argv[1], 'pid'), 'w').write(str(c.pid))\n"
            "print('postgresql://fake/daemon')\n"
        )
        Path(self.script).write_text(daemon, encoding="utf-8")
        pid_file = os.path.join(self.d, "pid")
        alive_check = [sys.executable, "-c", f"import os; os.kill(int(open({pid_file!r}).read()), 0); print('ALIVE')"]
        rc, out, _ = self.run_q(alive_check)
        self.assertEqual(rc, 0, out)
        self.assertIn("ALIVE", Path(self.log).read_text(encoding="utf-8"))
        pid = int(Path(pid_file).read_text(encoding="utf-8"))
        for _ in range(50):
            try:
                # the test process is the subreaper here: a killed orphan stays a zombie until reaped
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    break
            except ChildProcessError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the daemonized child must be killed when qrun exits")

    def test_start_and_run_share_one_deadline(self):
        # the start takes ~1 s of the 2 s budget: the run gets only the rest, so the whole call ends near 2 s, not 1 s + 2 s
        slow_start = "import sys, time\ntime.sleep(1.0)\nprint('postgresql://fake/slow')\n"
        Path(self.script).write_text(slow_start, encoding="utf-8")
        t0 = time.monotonic()
        with unittest.mock.patch.object(qrun, "_timeout", return_value=2.0):
            rc, _, _ = self.run_q([sys.executable, "-c", "import time; time.sleep(60)"])
        elapsed = time.monotonic() - t0
        self.assertNotEqual(rc, 0)
        self.assertLess(elapsed, 2.8, f"start and run must share one budget, took {elapsed:.2f} s")

    def test_start_command_failure_is_exit_2(self):
        self.write_cfg(qrun_pg_var="PGURL_TEST", qrun_pg_start=[sys.executable, "-c", "import sys; sys.exit(3)"])
        rc, _, err = self.run_q(CMD_READS_URL)
        self.assertEqual(rc, 2)
        self.assertIn("start command exit 3", err)


if __name__ == "__main__":
    unittest.main()
