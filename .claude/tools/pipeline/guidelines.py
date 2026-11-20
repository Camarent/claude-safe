"""Detect the languages of a repo from marker files in its root (top level only, no deep walk)."""

import fnmatch
import os

from pipeline.configuration import LANG_MARKERS


def _has(repo, patterns, subdirs):
    for sub in subdirs:
        try:
            names = os.listdir(os.path.join(repo, sub))
        except OSError:
            continue
        if any(fnmatch.fnmatch(n, p) for n in names for p in patterns):
            return True
    return False


def detect(repo):
    """-> the sorted language slugs whose marker files are in the repo root (cpp also looks in `src/`)."""
    return sorted(lang for lang, patterns, subdirs in LANG_MARKERS if _has(repo, patterns, subdirs))
