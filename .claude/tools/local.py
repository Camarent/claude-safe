"""Shim: `lp` (/usr/local/bin/lp) still points here; the real tool lives in search/local.py."""

import os
import sys

real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search", "local.py")
os.execv(sys.executable, [sys.executable, "-I", real, *sys.argv[1:]])
