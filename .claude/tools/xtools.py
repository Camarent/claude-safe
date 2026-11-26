#!/usr/bin/env python3
"""Extra-tools checker; the code lives in pipeline/extra_tools.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__":
    from pipeline.extra_tools import main

    main()
