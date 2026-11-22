"""Detect the languages of a repo from marker files in its root (top level only, no deep walk)."""

import fnmatch
import os

from pipeline.configuration import (
    BASE_GUIDELINE,
    GUIDELINES_DIGEST_MAX_LINES,
    GUIDELINES_DIR,
    GUIDELINES_EXT,
    LANG_EXTENSIONS,
    LANG_MARKERS,
)


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


def _langs_for(path):
    ext = os.path.splitext(path)[1].lower()
    return [lang for lang, exts in LANG_EXTENSIONS if ext in exts]


def digest(repo, changed_paths, guidelines_dir=GUIDELINES_DIR):
    """-> at most GUIDELINES_DIGEST_MAX_LINES lines for the changed file types: the base guideline and the <lang>.md of each
    changed type, each preceded by its full path. No matching type -> the full path list only. `repo` is the repo the paths
    belong to; the guideline files are shared, so only the paths select them."""
    langs = sorted({lang for path in changed_paths for lang in _langs_for(path)})
    if not langs:
        return [os.path.join(guidelines_dir, f) for f in sorted(os.listdir(guidelines_dir)) if f.endswith(GUIDELINES_EXT)]
    files = [BASE_GUIDELINE] + [lang + GUIDELINES_EXT for lang in langs]
    pointers = [f"full guideline: {os.path.join(guidelines_dir, f)}" for f in files]
    cap = GUIDELINES_DIGEST_MAX_LINES - len(pointers)
    body: list[str] = []
    for f in files:
        with open(os.path.join(guidelines_dir, f), encoding="utf-8") as fh:
            body.extend(line.rstrip("\n") for line in fh)
    if len(body) <= cap:
        return pointers + body
    return pointers + body[: cap - 1] + ["... cut: read the full guideline files above"]
