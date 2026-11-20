#!/usr/bin/env python3
"""Pure-Python wave driver entry point; the code and the usage text live in pipeline/cli_wave.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__":
    from pipeline.cli_feature import add_feature_command
    from pipeline.cli_wave import main

    sys.exit(main(extra_commands=(add_feature_command,)))
