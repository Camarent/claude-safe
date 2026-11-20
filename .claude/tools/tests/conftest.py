"""pytest session setup: one scratch TMPDIR for the whole run, removed at the end.

Tests (and the tools they start as child processes) create temp dirs and never remove them; a few suite runs used up all inodes of a tmpfs /tmp.
`ptest.py` does the same per test file."""

import os
import shutil
import tempfile

_SCRATCH = tempfile.mkdtemp(prefix="pytest-scratch-")
os.environ["TMPDIR"] = _SCRATCH
tempfile.tempdir = _SCRATCH
# No background `git maintenance` / gc in test repos (same as ptest.py QUIET_GIT).
if "GIT_CONFIG_COUNT" not in os.environ:
    os.environ.update(
        {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "gc.auto",
            "GIT_CONFIG_VALUE_0": "0",
            "GIT_CONFIG_KEY_1": "maintenance.auto",
            "GIT_CONFIG_VALUE_1": "false",
        }
    )


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus  # pytest hook signature; unused here
    shutil.rmtree(_SCRATCH, ignore_errors=True)
