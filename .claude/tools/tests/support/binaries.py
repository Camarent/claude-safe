"""requires_binary(name): skip decorator for tests that run a real binary. The skip reason names the binary, so a skipped test says what is missing
(`needs the gitleaks binary on PATH`) instead of passing unnoticed. CI installs the binaries its tests need (.github/workflows/ci.yml)."""

import shutil
import unittest


def requires_binary(name: str):
    """Skip the decorated test unless `name` is on PATH; the skip reason names the binary."""
    return unittest.skipUnless(shutil.which(name), f"needs the {name} binary on PATH (install it, or run where CI installs it)")
