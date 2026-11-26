#!/usr/bin/env python3
"""cmdindex: generate the `cs-*` command wrappers from `.claude/paths.json`.

python3 -I /root/.claude/tools/cmdindex.py gen-bin [--check]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline.cmdindex import main

if __name__ == "__main__":
    sys.exit(main())
