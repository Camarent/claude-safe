"""Temp dirs that are removed when the test ends, so a suite run leaves nothing in the temp dir."""

import shutil
import sys
import tempfile
import unittest
from types import FrameType


def _register(fn, *args, **kwargs) -> None:
    """Run fn(*args) when the calling test ends (caller's `self` TestCase), else when its module's tests end."""
    frame: FrameType | None = sys._getframe(2)
    try:
        while frame is not None:
            case = frame.f_locals.get("self")
            if isinstance(case, unittest.TestCase):
                case.addCleanup(fn, *args, **kwargs)
                return
            frame = frame.f_back
    finally:
        del frame
    unittest.addModuleCleanup(fn, *args, **kwargs)


def tracked_mkdtemp(*args, **kwargs) -> str:
    """tempfile.mkdtemp(...), removed when the calling test ends."""
    d = tempfile.mkdtemp(*args, **kwargs)
    _register(shutil.rmtree, d, ignore_errors=True)
    return d


def tracked_tmpdir(*args, **kwargs) -> tempfile.TemporaryDirectory:
    """tempfile.TemporaryDirectory(...), cleaned up when the calling test ends."""
    t = tempfile.TemporaryDirectory(*args, **kwargs)
    _register(t.cleanup)
    return t
