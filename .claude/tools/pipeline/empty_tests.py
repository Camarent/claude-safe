"""Empty-test check: the test functions of a Python test file that assert nothing. Pure: source text in, findings out (testrun.py reads the files and runs the gate)."""

import ast

from .configuration import EMPTY_TEST_MARKER_DEFAULT

ASSERT_PREFIXES = ("assert", "_assert")  # assertEqual, assertRaises, assert_called_once, _assert_ok, pytest-free helpers
CHECK_NAMES = ("raises", "fail")  # pytest.raises, self.fail


def _call_name(func):
    """The name a call targets: `f` for f(), `g` for x.g(), else None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _checks(node):
    """True when the subtree of NODE (nested defs included) holds an assert statement, an assert-style or raises/fail call, or a mock assertion."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            return True
        if isinstance(sub, ast.Call):
            name = _call_name(sub.func)
            if name and (name.startswith(ASSERT_PREFIXES) or name in CHECK_NAMES):
                return True
    return False


def _tests(tree):
    """The test functions of a module: module-level and directly inside a module-level class; the name starts with `test`."""
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        funcs.extend(n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return [f for f in funcs if f.name.startswith("test")]


def _marked(func, lines, marker):
    """True when the def line or the line above it holds the marker comment."""
    above = lines[func.lineno - 2] if func.lineno >= 2 else ""
    return marker in lines[func.lineno - 1] or marker in above


def find(source, path, marker=EMPTY_TEST_MARKER_DEFAULT):
    """-> [(name, line)] of the test functions of SOURCE (the text of file PATH) that hold no assertion and carry no marker. A syntax error -> []."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []
    lines = source.splitlines()
    return [(f.name, f.lineno) for f in _tests(tree) if not _checks(f) and not _marked(f, lines, marker)]
