"""Path-dependence scan of the test files a task changed (path_dependent_tests): a test that asserts on a worktree path or task id
(`task-N`, `/job/`) passes only in one checkout, so it breaks in every other. Found by AST, not a line regex: a plain data string that
holds the same text and is not an assert argument is not a finding. `path_dependent_tests` never raises."""

import ast
import os
import re

from .gitx import try_run

TEST_PREFIX = "test_"
TEST_SUFFIX = ".py"
ASSERT_METHODS = frozenset({"assertIn", "assertEqual", "assertNotIn", "assert"})
PATH_DEPENDENT = re.compile(r"task-\d+|/job/")


def _changed_tests(cwd, base):
    """Changed test_*.py files vs base (existing files only), sorted -> list of relative paths; [] when git fails."""
    r = try_run(cwd, "diff", "--name-only", "--diff-filter=d", base)
    if r is None or r.returncode != 0:
        return []
    names = [n for n in r.stdout.splitlines() if os.path.basename(n).startswith(TEST_PREFIX) and n.endswith(TEST_SUFFIX)]
    return sorted(n for n in names if os.path.isfile(os.path.join(cwd, n)))


def _literals(node):
    """String constants inside an assert node (an ast.Assert or an assert-method call), with their line numbers."""
    return [(n.lineno, n.value) for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _is_assert_method(call):
    """True for a call of an assert method on an object: self.assertIn(...)."""
    return isinstance(call.func, ast.Attribute) and call.func.attr in ASSERT_METHODS


def _asserts(tree):
    """Every assert node of the module: the statement `assert x` and calls to the assert methods (self.assertIn(...))."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) or (isinstance(node, ast.Call) and _is_assert_method(node)):
            yield node


def _file_findings(path, name):
    """`name:line: ...` for each assert in the file at path whose string literal matches PATH_DEPENDENT; unreadable file -> []."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=name)
    except (OSError, SyntaxError, ValueError):
        return []
    out = []
    for node in _asserts(tree):
        for lineno, text in _literals(node):
            m = PATH_DEPENDENT.search(text)
            if m:
                out.append(f"{name}:{node.lineno}: assert on path-dependent text {m.group(0)!r} (line {lineno}); use a relative or fixed value")
                break
    return out


def path_dependent_tests(cwd, base):
    """Path-dependent asserts in the test files changed vs base in the repo cwd -> `path:line: ...` lines, [] when none."""
    findings = []
    for name in _changed_tests(cwd, base):
        findings.extend(_file_findings(os.path.join(cwd, name), name))
    return findings
