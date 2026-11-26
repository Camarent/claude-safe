"""List public defs without a return annotation. Usage: python3 -I typing_check.py FILE... | --list LISTFILE (one path per line)

Exit 1 when any file has one. Public = name does not start with an underscore.
"""

import ast
import sys


def missing(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    return [
        f"{path}:{node.lineno} {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is None and not node.name.startswith("_")
    ]


def main(argv: list[str]) -> int:
    paths: list[str] = []
    args = iter(argv)
    for arg in args:
        if arg == "--list":
            with open(next(args), encoding="utf-8") as fh:
                paths += [ln.strip() for ln in fh if ln.strip()]
        else:
            paths.append(arg)
    found = [line for path in paths for line in missing(path)]
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
