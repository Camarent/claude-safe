import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.util import default_dir


class DefaultDirTest(unittest.TestCase):
    def test_nested_repo_resolves_to_workspace_with_pipeline_json(self):
        with tempfile.TemporaryDirectory() as ws:
            open(os.path.join(ws, "pipeline.json"), "w").close()
            nested = os.path.join(ws, "repo", "sub")
            os.makedirs(nested)
            old = os.getcwd()
            os.chdir(nested)
            try:
                self.assertEqual(os.path.realpath(default_dir()), os.path.realpath(ws))
            finally:
                os.chdir(old)

    def test_no_pipeline_json_falls_back_to_cwd_marker(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch("pipeline.util.os.path.isfile", return_value=False):  # a pipeline.json in a /tmp ancestor must not matter
                    self.assertEqual(default_dir(), ".")
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
