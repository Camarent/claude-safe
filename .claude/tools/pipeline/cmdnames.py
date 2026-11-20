"""Read-only view of the command index (`<home>/paths.json`): names, sides and the strings a matcher accepts.

Imports nothing from the pipeline, so `configuration` can use it without an import cycle; `cmdindex` re-exports it.
"""

import json
import os

INDEX_NAME = "paths.json"
DEFAULT_SIDE = "both"


def load(home):
    with open(os.path.join(home, INDEX_NAME), encoding="utf-8") as f:
        return json.load(f)


def side_of(entry):
    return entry.get("side", DEFAULT_SIDE)


def command_name(index, key):
    entry = index["commands"][key]
    return entry.get("name") or index["prefix"] + key


def commands(index, side=None):
    """{key: entry} for the commands that exist on `side` ('host' | 'container'); None = all."""
    return {k: e for k, e in index["commands"].items() if side is None or side_of(e) in (side, DEFAULT_SIDE)}


def names_for(index, key):
    """Every string a matcher accepts for a command: its name, the script's file name and the aliases."""
    entry = index["commands"][key]
    return [command_name(index, key), os.path.basename(entry["script"]), *entry.get("aliases", [])]
