#!/usr/bin/env python3
"""Thin orchestrator entry point; the code and the long usage text live in pipeline/cli_orch.py (shared helpers in pipeline/util.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__":
    from pipeline.cli_orch import main

    main()
