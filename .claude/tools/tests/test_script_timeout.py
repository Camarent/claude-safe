"""ORCH_SCRIPT_TIMEOUT: every reader (qrun, index_step, audit_run, final_check, driver) shares util.script_timeout."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import index_step
import qrun
from audit import audit_run, final_check

ENV = "ORCH_SCRIPT_TIMEOUT"
DEFAULTS = [(qrun, 1800.0), (audit_run, 1800.0), (final_check, 1800.0), (index_step, 900.0)]


class ScriptTimeoutTest(unittest.TestCase):
    def test_default_when_unset(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(ENV, None)
            for mod, default in DEFAULTS:
                self.assertEqual(mod._timeout(), default, mod.__name__)

    def test_env_value_wins(self):
        with mock.patch.dict(os.environ, {ENV: "12.5"}):
            for mod, _ in DEFAULTS:
                self.assertEqual(mod._timeout(), 12.5, mod.__name__)

    def test_garbage_gives_default_for_the_three_that_always_did(self):
        with mock.patch.dict(os.environ, {ENV: "soon"}):
            for mod, default in (DEFAULTS[0], DEFAULTS[2]):
                self.assertEqual(mod._timeout(), default, mod.__name__)


if __name__ == "__main__":
    unittest.main()
