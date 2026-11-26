#!/usr/bin/env python3
"""Generate docs/INDEX.md (file tree + public symbols per module) and check docs coverage.

    docs_index.py [--root DIR]           write DIR/docs/INDEX.md
    docs_index.py [--root DIR] --check   exit 1 if a module has no docs/modules/<slug>.md,
                                         a module doc points at a missing module, or
                                         docs/ARCHITECTURE.md is absent

The root must be the top of a git repository (it must contain .git). Otherwise the tool prints
NEEDS_ROOT and exits 2 (with --suggest, also nearby repositories), so the lead can ask the user which
one to use; --allow-no-git overrides this only when the user has explicitly approved docs outside a repo.

Modules are the top-level directories of the root, or the paths listed one per line in
docs/modules.txt when that file exists. A module's slug is its path with '/' replaced by '--'.
Output is deterministic (no timestamps) so regenerating it does not create noise.
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import gitx
from pipeline.util import swallowed
from search.ask_code import find_root

IGNORE_DIRS = {
    "docs",
    "node_modules",
    "target",
    "dist",
    "build",
    "out",
    "__pycache__",
    "venv",
    "env",
    "scratch",
    "vendor",
    "coverage",
    "site-packages",
}
MAX_FILE_BYTES = 1_000_000
MAX_FILES_PER_MODULE = 150
MAX_SYMBOLS_PER_FILE = 12
COLLAPSE_OVER = 8

SYMBOL_PATTERNS = {
    ".py": r"^(?:async\s+def|def|class)\s+([A-Za-z][A-Za-z0-9_]*)",
    ".js": r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)|^export\s+(?:const|let)\s+([A-Za-z_$][\w$]*)",
    ".mjs": r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)|^export\s+(?:const|let)\s+([A-Za-z_$][\w$]*)",
    ".ts": r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)|^export\s+(?:const|let)\s+([A-Za-z_$][\w$]*)",
    ".rs": r"^\s*pub(?:\([a-z]+\))?\s+(?:async\s+)?(?:fn|struct|enum|trait|mod|type|const)\s+([A-Za-z_][A-Za-z0-9_]*)",
    ".go": r"^func\s+(?:\([^)]*\)\s*)?([A-Z][A-Za-z0-9_]*)|^type\s+([A-Z][A-Za-z0-9_]*)",
    ".cpp": r"^\s*(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:[:{]|$)",
    ".hpp": r"^\s*(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:[:{]|$)",
    ".h": r"^\s*(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:[:{]|$)",
}
SOURCE_EXTS = set(SYMBOL_PATTERNS) | {".md", ".json", ".toml", ".yaml", ".yml", ".html", ".css", ".sh", ".sql", ".c", ".cc"}


def slug(module):
    return module.strip("/").replace("/", "--")


def ignored_paths(root, rels):
    """Subset of RELS (paths relative to ROOT) that git ignores; empty when ROOT is not a git work tree."""
    rels = list(rels)
    if not rels:
        return set()
    try:
        r = gitx.run(root, "check-ignore", "-z", "--stdin", input="\0".join(rels) + "\0")
    except OSError:
        return set()
    if r.returncode not in (0, 1):
        return set()
    return {p for p in r.stdout.split("\0") if p}


def modules_of(root, docs_dir="docs"):
    listed = Path(root, docs_dir, "modules.txt")
    if listed.is_file():
        lines = listed.read_text(encoding="utf-8").splitlines()
        mods = sorted(line.strip().strip("/") for line in lines if line.strip() and not line.startswith("#"))
    else:
        mods = sorted(p.name for p in Path(root).iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in IGNORE_DIRS)
    skip = ignored_paths(root, mods)
    return [m for m in mods if m not in skip]


def module_headings(root, docs_dir="docs"):
    """Module names that get an index heading, in index order (the same list build_index uses)."""
    return modules_of(root, docs_dir)


def walk(root, module):
    base = os.path.join(root, module)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in IGNORE_DIRS)
        skip = ignored_paths(root, [os.path.relpath(os.path.join(dirpath, d), root) for d in dirnames])
        dirnames[:] = [d for d in dirnames if os.path.relpath(os.path.join(dirpath, d), root) not in skip]
        cands = []
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.splitext(name)[1] in SOURCE_EXTS and os.path.getsize(path) <= MAX_FILE_BYTES:
                cands.append(os.path.relpath(path, root))
        skip = ignored_paths(root, cands)
        for rel in cands:
            if rel not in skip:
                yield rel


def scan(root, rel):
    ext = os.path.splitext(rel)[1]
    pattern = re.compile(SYMBOL_PATTERNS[ext]) if ext in SYMBOL_PATTERNS else None
    lines, symbols = 0, []
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            for line in f:
                lines += 1
                if pattern:
                    m = pattern.match(line)
                    if m:
                        symbols.append(next(g for g in m.groups() if g))
    except OSError as e:
        swallowed("docs_index.scan", e)  # keep the lines and symbols read so far
    return lines, symbols


def _file_entry(rel, n, symbols):
    extra = ""
    if symbols:
        head = ", ".join(symbols[:MAX_SYMBOLS_PER_FILE])
        more = len(symbols) - MAX_SYMBOLS_PER_FILE
        extra = f" - {head}" + (f", +{more} more" if more > 0 else "")
    return f"- `{rel}` ({n}){extra}"


def _collapsed_groups(scanned):
    """{(dir, ext): rows} of the symbol-less files that sit in groups bigger than COLLAPSE_OVER."""
    groups: defaultdict = defaultdict(list)
    for row in scanned:
        ext = os.path.splitext(row[0])[1]
        if ext not in SYMBOL_PATTERNS:
            groups[(os.path.dirname(row[0]), ext)].append(row)
    return {k: v for k, v in groups.items() if len(v) > COLLAPSE_OVER}


def _module_section(root, module):
    files = list(walk(root, module))
    scanned = [(rel, *scan(root, rel)) for rel in files]
    total = sum(n for _, n, _ in scanned)
    out = [f"## {module}  (`{slug(module)}`)", "", f"{len(files)} files, {total} lines", ""]
    collapsed = _collapsed_groups(scanned)
    rest = [r for r in scanned if (os.path.dirname(r[0]), os.path.splitext(r[0])[1]) not in collapsed]
    for (d, ext), rows in sorted(collapsed.items()):
        out.append(f"- `{d}/*{ext}` ({len(rows)} files, {sum(r[1] for r in rows)} lines)")
    shown = sorted(rest, key=lambda r: (-r[1], r[0]))[:MAX_FILES_PER_MODULE]
    out += [_file_entry(*row) for row in sorted(shown)]
    if len(rest) > len(shown):
        out.append(f"- ... {len(rest) - len(shown)} smaller files omitted")
    out.append("")
    return out


def build_index(root, docs_dir="docs"):
    out = [
        "# Index",
        "",
        (
            "Generated by `docs_index.py` - do not edit by hand. One section per module; "
            "see `docs/modules/<slug>.md` for the contract and `docs/ARCHITECTURE.md` for the map."
        ),
        "",
    ]
    for module in modules_of(root, docs_dir):
        out += _module_section(root, module)
    return "\n".join(out)


def check(root, docs_dir="docs"):
    problems = []
    docs = os.path.join(root, docs_dir)
    if not os.path.isfile(os.path.join(docs, "ARCHITECTURE.md")):
        problems.append(f"missing {docs_dir}/ARCHITECTURE.md")
    mods = modules_of(root, docs_dir)
    for m in mods:
        if not os.path.isfile(os.path.join(docs, "modules", slug(m) + ".md")):
            problems.append(f"module '{m}' has no {docs_dir}/modules/{slug(m)}.md")
    mod_dir = os.path.join(docs, "modules")
    known = {slug(m) for m in mods}
    if os.path.isdir(mod_dir):
        for name in sorted(os.listdir(mod_dir)):
            if name.endswith(".md") and name[:-3] not in known:
                problems.append(f"{docs_dir}/modules/{name} describes no existing module - fold it into its parent module doc")
    problems += check_references(root, mods, docs_dir)
    return problems


REF_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?")
COMMON_PORTS = {80, 443, 3000, 5000, 5432, 6379, 8000, 8080, 8443, 9000}


def _is_address(rel, port):
    """True for an IP (`127.0.0.1:8080`), a URL host (`http://h.io:9000`) or a bare host with a common port (`host.com:80`)."""
    if re.fullmatch(r"\d+(\.\d+){3}", rel) or rel.startswith("//"):
        return True
    return "/" not in rel and port in COMMON_PORTS


def check_references(root, mods, docs_dir="docs"):
    """Every `path:line[-line]` cited in docs/ must name an existing file and a line inside it."""
    problems, docs = [], os.path.join(root, docs_dir)
    line_counts = {}

    def length(path):
        if path not in line_counts:
            with open(path, encoding="utf-8", errors="replace") as f:
                line_counts[path] = sum(1 for _ in f)
        return line_counts[path]

    for dirpath, _, names in os.walk(docs):
        for name in sorted(names):
            if not name.endswith(".md") or name == "INDEX.md":
                continue
            doc = os.path.join(dirpath, name)
            with open(doc, encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in REF_RE.finditer(text):
                rel, first, last = m.group(1), int(m.group(2)), m.group(3)
                if _is_address(rel, first):
                    continue
                candidates = [os.path.join(root, rel)] + [os.path.join(root, mod, rel) for mod in mods]
                path = next((c for c in candidates if os.path.isfile(c)), None)
                where = os.path.relpath(doc, root)
                if path is None:
                    problems.append(f"{where}: reference {m.group(0)} - file not found")
                elif max(first, int(last or first)) > length(path):
                    problems.append(f"{where}: reference {m.group(0)} - file has only {length(path)} lines")
    return problems


def git_candidates(root):
    """Git repo roots near ROOT: the enclosing repo (if any) and repos up to two levels below."""
    found, top = [], find_root(root)
    if os.path.exists(os.path.join(top, ".git")):
        found.append(top)
    for dirpath, dirnames, _ in os.walk(root):
        depth = os.path.relpath(dirpath, root).count(os.sep) + (0 if dirpath == root else 1)
        if ".git" in dirnames or os.path.exists(os.path.join(dirpath, ".git")):
            if dirpath != root:
                found.append(dirpath)
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")] if depth < 2 else []
    return sorted(set(found))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--docs-dir", default="docs", help="docs folder under --root (default: docs)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="exit 1 when <docs-dir>/INDEX.md is missing or differs from the index built now; writes nothing")
    ap.add_argument("--allow-no-git", action="store_true")
    ap.add_argument("--suggest", action="store_true", help="with NEEDS_ROOT, also list nearby git repos (for the lead, not for docs-writer)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not args.allow_no_git and not os.path.exists(os.path.join(root, ".git")):
        print(f"NEEDS_ROOT: {root} is not the top of a git repository. Stop and report this to the lead; do not pick another root.")
        if args.suggest:
            cands = git_candidates(root)
            print("Candidates: " + (", ".join(cands) if cands else "none found - ask the user where the repo is"))
        sys.exit(2)
    if args.check:
        problems = check(root, args.docs_dir)
        print("\n".join(problems) if problems else "docs OK")
        sys.exit(1 if problems else 0)
    text = build_index(root, args.docs_dir)
    index = Path(root, args.docs_dir, "INDEX.md")
    if args.fresh:
        current = index.read_text(encoding="utf-8") if index.is_file() else None
        if current != text + "\n":
            print(f"{args.docs_dir}/INDEX.md is stale - regenerate it with docs_index.py --root {root}")
            sys.exit(1)
        print(f"{args.docs_dir}/INDEX.md is fresh")
        sys.exit(0)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {args.docs_dir}/INDEX.md ({text.count(chr(10))} lines, {len(modules_of(root, args.docs_dir))} modules)")


if __name__ == "__main__":
    main()
