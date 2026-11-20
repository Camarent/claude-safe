"""timing_sensitive: decorator for tests that start real processes or wait on clocks and sockets. They flake on a loaded host (a code-index build, the local model), so they are
off by default and run only with TIMING_TESTS=1 (`TIMING_TESTS=1 python3 -I ptest.py ...`)."""

import os
import unittest

ENV_TIMING_TESTS = "TIMING_TESTS"

timing_sensitive = unittest.skipUnless(os.environ.get(ENV_TIMING_TESTS) == "1", f"timing sensitive: set {ENV_TIMING_TESTS}=1 to run")
