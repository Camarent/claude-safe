"""docs_index.check_references: host:port and IP addresses are not file references (temp dir)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from docs_index import check_references


class TestAddressRefs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        os.makedirs(os.path.join(self._tmp, "docs"))

    def _doc(self, text):
        with open(os.path.join(self._tmp, "docs", "guide.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return check_references(self._tmp, [])

    def test_ip_and_host_port_are_not_refs(self):
        text = "Serve on 127.0.0.1:8080. Proxy host.com:80 and http://api.example.io:9000/x.\n"
        self.assertEqual(self._doc(text), [])

    def test_missing_file_ref_still_flagged(self):
        problems = self._doc("See src/missing.py:3 for details.\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("src/missing.py:3", problems[0])


if __name__ == "__main__":
    unittest.main()
