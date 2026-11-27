"""Single home for pipeline constants: verdict words, roles and their allowed verdicts, limits, headless-session flags, and the per-project
settings read from `<workspace>/pipeline.json`. Other modules import from here; do not repeat these literals elsewhere.

A package: each submodule holds one topic. This file resolves every name lazily (module `__getattr__`), so
`from pipeline.configuration import X` keeps working without importing all twenty submodules up front."""

import importlib
import subprocess  # noqa: F401  (tests patch configuration.subprocess)
from typing import Any

# `project` is also a submodule name: import the function now, so the package attribute is the function, not the module.
from .project import project  # noqa: F401  (re-export: shadows the submodule attribute of the same name)

# Submodules searched for a name, in order. A name is served by the first submodule that defines it.
_SUBMODULES = (
    "ci_poll",
    "cite_check",
    "cleanup",
    "defaults",
    "env",
    "errors",
    "flags",
    "gitbranch",
    "layout",
    "limits",
    "literals",
    "paths",
    "planning",
    "project",
    "public",
    "publish",
    "qrun",
    "serve",
    "tuning",
    "verdicts",
)


def __getattr__(name: str) -> Any:
    """Return the constant or function `name` from the first submodule that has it; cache it in this module."""
    for sub in _SUBMODULES:
        module = importlib.import_module(f".{sub}", __name__)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the module globals plus every public name of every submodule, so tab completion and `dir()` see them."""
    names = set(globals())
    for sub in _SUBMODULES:
        names.update(n for n in dir(importlib.import_module(f".{sub}", __name__)) if not n.startswith("__"))
    return sorted(names)
