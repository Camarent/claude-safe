"""Orphan check: a source file added since the base under a `wiring_dirs` folder that no other tracked source file imports is an orphan.
Pure text scan (no build graph): each import line names a module, the module is resolved to a repo path (a relative specifier from the importing file's folder), and a candidate counts as imported when another tracked file's import resolves to it.
Test files and the knip `entry` globs are never orphans. `main` writes the report and always exits 0: the check warns, it never blocks."""

import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys

from . import gitx
from .configuration import (
    KNIP_CONFIG_FILE,
    KNIP_ENTRY_KEY,
    WIRING_ALIAS_PREFIXES,
    WIRING_DIRS_KEY,
    WIRING_IMPORT_LINE_RE,
    WIRING_NONE_TEXT,
    WIRING_ORPHAN_LINE,
    WIRING_PY_EXT,
    WIRING_PY_FROM_RE,
    WIRING_PY_IMPORT_RE,
    WIRING_QUOTED_RE,
    WIRING_SOURCE_EXTS,
    WIRING_SPLIT_RE,
    WIRING_TEST_RE,
    ConfigError,
    project,
    repo_dir,
)


def _under(path: str, dirs) -> bool:
    """-> True when PATH is one of DIRS or lies below one of them."""
    return any(path.startswith(d.rstrip("/") + "/") for d in dirs)


def _entry_globs(repo) -> list[str]:
    """-> the `entry` globs of the repo's knip.json ([] when the file is missing or has no list)."""
    try:
        with open(os.path.join(repo, KNIP_CONFIG_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    entry = data.get(KNIP_ENTRY_KEY) if isinstance(data, dict) else None
    return [e for e in entry if isinstance(e, str)] if isinstance(entry, list) else []


def _z_out(repo, *args) -> list[str]:
    """-> the paths of a git call that prints them NUL-separated (`-z`: names are never quoted, so non-ASCII paths stay whole)."""
    return [os.fsdecode(p) for p in gitx.raw_out(repo, *args).split(b"\0") if p]


def _module_specs(line: str, importer: str) -> list[str]:
    """-> the module specifiers an import LINE of file IMPORTER names, as written ([] for a line that is not an import)."""
    if not re.match(WIRING_IMPORT_LINE_RE, line):
        return []
    if not importer.endswith(WIRING_PY_EXT):
        return re.findall(WIRING_QUOTED_RE, line)  # JS/TS: the quoted module paths
    m = re.match(WIRING_PY_FROM_RE, line)
    if m:  # Python: `from X import a, b` names module X; a and b may be submodules of X (`from . import a` gives `.a`)
        base = m.group(1) if m.group(1).endswith(".") else m.group(1) + "."
        return [m.group(1)] + [base + t for t in re.split(WIRING_SPLIT_RE, m.group(2)) if t]
    m = re.match(WIRING_PY_IMPORT_RE, line)
    return [m.group(1)] if m else []


def _file_key(path: str) -> str:
    """-> the repo PATH without its source extension: the key an import of that file resolves to."""
    root, ext = posixpath.splitext(path)
    return root if ext in WIRING_SOURCE_EXTS else path


def _module_key(spec: str, importer: str) -> str:
    """-> the repo path (see _file_key) that module SPEC names when file IMPORTER imports it."""
    if importer.endswith(WIRING_PY_EXT):  # Python: leading dots are the package level, dots inside are folders
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec[dots:].replace(".", "/")
        spec = (("./" if dots == 1 else "../" * (dots - 1)) + rest) if dots else rest
    alias = next((a for a in WIRING_ALIAS_PREFIXES if spec.startswith(a)), None)
    if alias is not None:  # a path alias names a repo path from the root
        path = spec[len(alias) :]
    elif spec.startswith("."):  # relative: joined to the importing file's folder
        path = posixpath.join(posixpath.dirname(importer), spec)
    else:  # a bare path: a repo path from the root
        path = spec
    return _file_key(posixpath.normpath(path))


def orphans(repo, base, dirs) -> list[str]:
    """-> the source files added between BASE and HEAD under DIRS that no other tracked source file imports (sorted; [] when none)."""
    entries = _entry_globs(repo)
    added = _z_out(repo, "diff", "-z", "--name-only", "--diff-filter=A", f"{base}..HEAD")
    candidates = [
        p
        for p in added
        if p.endswith(WIRING_SOURCE_EXTS) and _under(p, dirs) and not re.search(WIRING_TEST_RE, p) and not any(fnmatch.fnmatchcase(p, g) for g in entries)
    ]
    if not candidates:
        return []
    importers: dict[str, set[str]] = {}  # resolved module key -> the tracked files whose import lines name it
    for p in _z_out(repo, "ls-files", "-z"):
        if not p.endswith(WIRING_SOURCE_EXTS):
            continue
        try:
            with open(os.path.join(repo, p), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue  # a tracked file missing from the work tree names nothing
        for line in text.splitlines():
            for spec in _module_specs(line, p):
                importers.setdefault(_module_key(spec, p), set()).add(p)
    return sorted(c for c in candidates if not (importers.get(_file_key(c), set()) - {c}))


def main(argv=None) -> int:
    """-> 0 always. Writes one `- orphan: <path>` line per orphan (or WIRING_NONE_TEXT) to --out and warns on stderr; a broken config or a failed git call warns and writes nothing."""
    ap = argparse.ArgumentParser(description="warn about new source files that nothing imports")
    ap.add_argument("--workspace", required=True, help="the workspace folder holding pipeline.json")
    ap.add_argument("--base", required=True, help="the commit the stage started from")
    ap.add_argument("--out", required=True, help="the report file to write")
    args = ap.parse_args(argv)
    try:
        cfg = project(args.workspace)
        found = orphans(repo_dir(args.workspace, cfg), args.base, cfg.get(WIRING_DIRS_KEY) or [])
        text = "".join(WIRING_ORPHAN_LINE.format(p) + "\n" for p in found) if found else WIRING_NONE_TEXT
        with open(args.out, "w", encoding="utf-8", errors="backslashreplace") as f:
            f.write(text)
    except (ConfigError, OSError, subprocess.SubprocessError) as e:
        print(f"warning: orphan check skipped: {e}", file=sys.stderr)
        return 0
    if found:
        print(f"warning: {len(found)} orphan file(s), see {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
