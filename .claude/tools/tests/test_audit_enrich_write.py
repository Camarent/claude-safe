"""audit_enrich.main: a crash while writing leaves the findings file intact (temp dir, no network)."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_enrich as E

FINDINGS = {"findings": [{"file": "a.js", "msg": "x"}]}


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "findings.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(FINDINGS, fh)

    def test_crash_during_write_keeps_original(self):
        def partial_then_crash(obj, fh, **kw):
            fh.write('{"findings": [')
            raise OSError("disk full")

        check = mock.patch.object(E, "_check_of", return_value="unused")
        dump = mock.patch.object(E.json, "dump", side_effect=partial_then_crash)
        with check, dump, self.assertRaises(OSError):
            E.main([self._tmp.name, "--findings", self.path])
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), FINDINGS)

    def test_success_writes_check(self):
        with mock.patch.object(E, "_check_of", return_value="unused"):
            E.main([self._tmp.name, "--findings", self.path])
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["findings"][0]["check"], "unused")
        self.assertEqual(os.listdir(self._tmp.name), ["findings.json"])


if __name__ == "__main__":
    unittest.main()
