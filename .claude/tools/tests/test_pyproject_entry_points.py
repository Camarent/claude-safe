"""pyproject metadata and [project.scripts]: one entry per cs-* wrapper whose target has a top-level main."""

import os
import re
import subprocess
import sys
import tomllib
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TOOLS = os.path.join(REPO, ".claude", "tools")
BIN = os.path.join(REPO, ".claude", "bin")
TARGET_RE = re.compile(r'^target="\$here/\.\./tools/(.+)"$', re.MULTILINE)
MAIN_RE = re.compile(r"^def main\(", re.MULTILINE)


def _load_pyproject():
    with open(os.path.join(REPO, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def _wrapper_targets():
    """cs-* wrapper name -> target path relative to .claude/tools (lp is not a cs-* wrapper)."""
    out = {}
    for name in sorted(os.listdir(BIN)):
        if not name.startswith("cs-"):
            continue
        with open(os.path.join(BIN, name), encoding="utf-8") as f:
            m = TARGET_RE.search(f.read())
        if m:
            out[name] = m.group(1)
    return out


def _expected_entries():
    """Entries the wrappers imply: target is a .py module that defines a top-level main."""
    expected = {}
    for name, rel in _wrapper_targets().items():
        path = os.path.join(TOOLS, rel)
        if not rel.endswith(".py") or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            if not MAIN_RE.search(f.read()):
                continue
        module = rel[: -len(".py")].replace(os.sep, ".").replace("/", ".")
        expected[name] = f"{module}:main"
    return expected


def _import_check(targets):
    """Import each target from .claude/tools in a fresh interpreter; return the ones that fail or are not callable."""
    code = (
        "import importlib, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "bad = []\n"
        "for spec in sys.argv[2:]:\n"
        "    mod, attr = spec.split(':')\n"
        "    try:\n"
        "        fn = getattr(importlib.import_module(mod), attr)\n"
        "        if not callable(fn):\n"
        "            bad.append(spec + ' not callable')\n"
        "    except Exception as e:\n"
        "        bad.append(spec + ' ' + type(e).__name__ + ': ' + str(e))\n"
        "print('\\n'.join(bad))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code, TOOLS, *targets],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return [f"import helper failed: {proc.stderr.strip()[-300:]}"]
    return [line for line in proc.stdout.splitlines() if line]


class T(unittest.TestCase):
    def test_c1_metadata(self):
        project = _load_pyproject()["project"]
        self.assertEqual(project["version"], "0.1.0")
        self.assertEqual(project["license"], "MIT")
        self.assertEqual(project["authors"], [{"name": "Camarent"}])
        self.assertEqual(project["readme"], "README.md")
        self.assertEqual(
            project["urls"],
            {
                "Homepage": "https://github.com/Camarent/claude-safe",
                "Repository": "https://github.com/Camarent/claude-safe",
                "Issues": "https://github.com/Camarent/claude-safe/issues",
            },
        )
        self.assertTrue(os.path.isfile(os.path.join(REPO, project["readme"])))

    def test_c2_scripts_match_wrappers_with_main(self):
        scripts = _load_pyproject()["project"]["scripts"]
        expected = _expected_entries()
        self.assertTrue(expected, "no cs-* wrapper target defines main: the scan is broken")
        self.assertEqual(scripts, expected)

    def test_c2_every_entry_imports_and_is_callable(self):
        scripts = _load_pyproject()["project"]["scripts"]
        self.assertEqual(_import_check(sorted(scripts.values())), [])

    def test_c2_package_false_stays(self):
        self.assertIs(_load_pyproject()["tool"]["uv"]["package"], False)

    def test_c3_mutated_pyproject_entry_fails_import(self):
        # Mutation on an in-memory copy of pyproject.toml: rename one target attr, the import check must report it.
        with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as f:
            raw = f.read()
        mutated = raw.replace('cs-ledger = "retro.ledger:main"', 'cs-ledger = "retro.ledger:no_such_main"')
        self.assertNotEqual(mutated, raw)
        scripts = tomllib.loads(mutated)["project"]["scripts"]
        bad = _import_check(sorted(scripts.values()))
        self.assertEqual(len(bad), 1)
        self.assertIn("retro.ledger:no_such_main", bad[0])
        self.assertIn("AttributeError", bad[0])


if __name__ == "__main__":
    unittest.main()
