"""Run a test with an empty temp dir as cwd, so code that reads ./pipeline.json never sees the caller's repo."""

import os
import shutil
import tempfile
import unittest


def isolate_cwd(case: unittest.TestCase) -> str:
    """chdir into a fresh empty dir until the test ends; -> that dir."""
    old = os.getcwd()
    d = tempfile.mkdtemp(prefix="isolated-cwd-")
    os.chdir(d)

    def restore():
        os.chdir(old)
        shutil.rmtree(d, ignore_errors=True)

    case.addCleanup(restore)
    return d
