"""cli_orch.warm_index: background indexer start, once-a-minute stamp, off switch."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch
from pipeline.configuration import ENV_NO_INDEX


class WarmIndexTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        self.a = SimpleNamespace(dir=self.d, stage="s")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV_NO_INDEX, None)

    def popen(self):
        return mock.patch.object(cli_orch.subprocess, "Popen")

    def test_starts_the_indexer_once_per_minute(self):
        with self.popen() as p:
            cli_orch.warm_index(self.a)
            cli_orch.warm_index(self.a)  # stamp is fresh: no second start
        self.assertEqual(p.call_count, 1)
        cmd = p.call_args.args[0]
        self.assertEqual(cmd[-3:], ["index", "--root", self.d])
        self.assertTrue(p.call_args.kwargs["start_new_session"])
        self.assertTrue(os.path.exists(os.path.join(self.d, ".work", "s", ".index-warm")))

    def test_old_stamp_starts_it_again(self):
        stamp = os.path.join(self.d, ".work", "s", ".index-warm")
        os.makedirs(os.path.dirname(stamp))
        open(stamp, "w").close()
        os.utime(stamp, (1, 1))
        with self.popen() as p:
            cli_orch.warm_index(self.a)
        self.assertEqual(p.call_count, 1)

    def test_off_switch_and_missing_dir_start_nothing(self):
        with self.popen() as p:
            os.environ[ENV_NO_INDEX] = "1"
            cli_orch.warm_index(self.a)
            del os.environ[ENV_NO_INDEX]
            cli_orch.warm_index(SimpleNamespace(dir=os.path.join(self.d, "nope"), stage="s"))
        p.assert_not_called()

    def test_start_failure_never_raises(self):
        with mock.patch.object(cli_orch.subprocess, "Popen", side_effect=OSError("no fork")):
            self.assertIsNone(cli_orch.warm_index(self.a))


if __name__ == "__main__":
    unittest.main()
