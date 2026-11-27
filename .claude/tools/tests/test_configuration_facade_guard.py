"""Guard for the pipeline.configuration facade: every submodule is listed, no public name is defined twice with different values,
and every `from pipeline.configuration import X` (or `from .configuration import X`) under .claude/tools resolves."""

import ast
import importlib
import os
import sys
import unittest
from types import ModuleType
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(TOOLS_DIR, "pipeline", "configuration")
# pr_gates: imported by its full path (pipeline.configuration.pr_gates), never through the facade, so it need not be listed.
EXEMPT = {"pr_gates"}


def _config_files() -> set[str]:
    """Stems of every .py in the configuration package except __init__."""
    return {name[:-3] for name in os.listdir(CONFIG_DIR) if name.endswith(".py") and name != "__init__.py"}


def _unlisted(listed) -> set[str]:
    """Submodule files that neither `listed` nor EXEMPT names."""
    return _config_files() - set(listed) - EXEMPT


def _public_values(configuration) -> dict[str, list[tuple[str, object]]]:
    """Map each public name to (submodule, value) for every submodule that defines it (module objects skipped)."""
    found: dict[str, list[tuple[str, object]]] = {}
    for sub in configuration._SUBMODULES:
        module = importlib.import_module(f".{sub}", configuration.__name__)
        for name, value in vars(module).items():
            if name.startswith("_") or isinstance(value, ModuleType):
                continue
            found.setdefault(name, []).append((sub, value))
    return found


def _same(a: object, b: object) -> bool:
    try:
        return a is b or bool(a == b)
    except Exception:
        # A value whose __eq__ raises counts as different.
        return False


def _importer_names() -> list[tuple[str, str]]:
    """(file, name) for every `from pipeline.configuration import name` or `from .configuration import name` under TOOLS_DIR."""
    pairs: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(TOOLS_DIR):
        dirs[:] = [d for d in dirs if not d.startswith("__pycache__")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                absolute = node.level == 0 and node.module == "pipeline.configuration"
                relative = node.level == 1 and node.module == "configuration"
                if absolute or relative:
                    pairs.extend((path, alias.name) for alias in node.names if alias.name != "*")
    return pairs


class ConfigurationFacadeGuardTest(unittest.TestCase):
    def test_every_submodule_listed(self):
        import pipeline.configuration as configuration

        self.assertEqual(sorted(_unlisted(configuration._SUBMODULES)), [])
        self.assertEqual(sorted(set(configuration._SUBMODULES) - _config_files()), [])

    def test_removed_entry_is_reported_as_unlisted(self):
        # Mutation check: dropping one entry from _SUBMODULES must make the C1 check fail.
        import pipeline.configuration as configuration

        dropped = configuration._SUBMODULES[0]
        mutated = tuple(s for s in configuration._SUBMODULES if s != dropped)
        with mock.patch.object(configuration, "_SUBMODULES", mutated):
            self.assertEqual(_unlisted(configuration._SUBMODULES), {dropped})

    def test_no_conflicting_names(self):
        import pipeline.configuration as configuration

        conflicts = []
        for name, defs in _public_values(configuration).items():
            first_sub, first_value = defs[0]
            for sub, value in defs[1:]:
                if not _same(first_value, value):
                    conflicts.append(f"{name}: {first_sub} vs {sub}")
        self.assertEqual(conflicts, [])

    def test_importers_resolve(self):
        import pipeline.configuration as configuration

        unresolved = []
        for path, name in _importer_names():
            if name in configuration._SUBMODULES or name in _config_files():
                continue
            if not hasattr(configuration, name):
                unresolved.append(f"{os.path.relpath(path, TOOLS_DIR)}: {name}")
        self.assertEqual(unresolved, [])
