"""Agent files: the types (description, default model) found in the agents dir."""

import os

from .configuration import DEFAULT_AGENTS_DIR, ENV_AGENTS_DIR


def agent_types():
    """-> {name: one-line description} from the agents dir, or None when it does not exist (tests)."""
    d = os.environ.get(ENV_AGENTS_DIR, DEFAULT_AGENTS_DIR)
    if not os.path.isdir(d):
        return None
    out = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".md"):
            desc, model = "", ""
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("description:") and not desc:
                        desc = line[12:].strip(" >")[:110]
                    if line.startswith("model:"):
                        model = line[6:].strip()
            out[f[:-3]] = {"desc": desc, "model": model}
    return out
