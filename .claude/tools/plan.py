#!/usr/bin/env python3
"""Thin plan entry point; the code and the usage text live in pipeline/cli_plan.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__":
    from pipeline.cli_plan import main

    sys.exit(main())
