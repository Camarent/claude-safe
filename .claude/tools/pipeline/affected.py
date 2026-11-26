"""Affected tests: the test files that import the module of each planned path (AST scan, stdlib only).

The architect runs it on the planned paths and pastes the result into the task's `Affected tests:` line:

    python3 -I .claude/tools/pipeline/affected.py .claude/tools/pipeline/plan_review.py

A planned path maps to a dotted module name relative to the source root (`.claude/tools`): `pipeline.plan_review`.
A test imports it when its `import` or `from ... import` names that module (or a name inside it)."""

import ast
import os
import sys


def module_name(path, root) -> str:
    """-> dotted module of a planned path under root, '' when the path is not under root."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    if rel.startswith(os.pardir) or not rel.endswith(".py"):
        return ""
    parts = rel[: -len(".py")].split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_names(source) -> set[str]:
    """-> every absolute module name and `module.name` a test source imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def importers(paths, tests_dir, root) -> list[str]:
    """-> sorted test files under tests_dir (relative to it) that import a module of any planned path."""
    wanted = {m for m in (module_name(p, root) for p in paths) if m}
    if not wanted:
        return []
    found = []
    for dirpath, _, files in os.walk(tests_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            with open(full, encoding="utf-8") as fh:
                if imported_names(fh.read()) & wanted:
                    found.append(os.path.relpath(full, tests_dir))
    return sorted(found)


def main(argv) -> int:
    """CLI: prints the affected test files of the planned paths as one comma-separated line."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(",".join(importers(argv, os.path.join(root, "tests"), root)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
