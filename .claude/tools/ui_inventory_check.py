"""Check a UI inventory: every screen reachable, every link working, every term in the glossary.

    python3 -I .claude/tools/ui_inventory_check.py INVENTORY.json

A script, no model. The inventory is JSON: {"glossary": [terms], "entry": screen-id, "screens": [{"id", "title",
"data_source", "controls": [{"label", "goto"}], "exits": [screen-id], "terms": [words]}]}. Exit 1 with one line per
problem: `unreachable:` for a screen not reached from the entry through control `goto` and `exits`, `dead-link:` for a
`goto` or exit that names no screen id, `term:` for a word of a title, control label or term not in the glossary
(case-insensitive), `missing:` for a screen without `data_source` or without controls or exits. Exit 0 otherwise.
"""

import argparse
import json
import re
import sys

# A word is a run of Unicode letters or digits, with inner apostrophes kept (don't); `_` is not a word character.
WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def load(path: str) -> tuple[dict | None, str | None]:
    """-> (inventory, None) or (None, problem) when PATH is unreadable or not the inventory shape."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        return None, f"invalid: cannot read {path} ({e.strerror})"
    except ValueError as e:
        return None, f"invalid: {path} is not valid JSON ({e})"
    if not isinstance(data, dict) or not isinstance(data.get("screens"), list) or not isinstance(data.get("glossary"), list):
        return None, f"invalid: {path} needs a `screens` list and a `glossary` list"
    if not all(isinstance(s, dict) and isinstance(s.get("id"), str) for s in data["screens"]):
        return None, f"invalid: every screen in {path} needs a string `id`"
    if data.get("entry") is not None and not isinstance(data.get("entry"), str):
        return None, f"invalid: `entry` in {path} must be a string"
    return data, None


def _targets(screen):
    """-> every screen id this screen links to: control `goto` values, then exits. Values may be bad (dead links)."""
    controls = [c for c in screen.get("controls") or [] if isinstance(c, dict)]
    return [c.get("goto") for c in controls] + list(screen.get("exits") or [])


def reachable(entry: str | None, screens: dict) -> set[str]:
    """-> set of screen ids reached from ENTRY by following control gotos and exits."""
    seen: set[str] = set()
    todo = [entry] if entry is not None and entry in screens else []
    while todo:
        sid = todo.pop()
        if sid in seen:
            continue
        seen.add(sid)
        todo += [t for t in _targets(screens[sid]) if isinstance(t, str) and t in screens]
    return seen


def _term_problems(sid, s, glossary):
    """-> `term:` lines for words of the title, control labels and terms that are not in GLOSSARY."""
    labels = [str(c.get("label", "")).casefold() for c in s.get("controls") or [] if isinstance(c, dict)]
    bad = {w for text in [str(s.get("title", "")).casefold(), *labels] for w in WORD_RE.findall(text) if w not in glossary}
    bad |= {str(t).casefold() for t in s.get("terms") or [] if str(t).casefold() not in glossary}
    return [f"term: {sid} {w!r} not in glossary" for w in sorted(bad)]


def _screen_problems(sid, s, seen, glossary, screens):
    """-> problem lines for one screen SID; SEEN is the set of screens reachable from the entry."""
    out = [] if sid in seen else [f"unreachable: {sid}"]
    out += [f"dead-link: {sid} -> {t!r}" for t in _targets(s) if not isinstance(t, str) or t not in screens]
    if not s.get("data_source") or not (s.get("controls") or s.get("exits")):
        out.append(f"missing: {sid} needs a data_source and controls or exits")
    return out + _term_problems(sid, s, glossary)


def problems(inv: dict) -> list[str]:
    """-> list of problem lines for the inventory INV (empty when clean)."""
    screens = {s["id"]: s for s in inv["screens"]}
    glossary = {str(g).casefold() for g in inv["glossary"]}
    entry = inv.get("entry")
    out = [] if entry in screens else [f"dead-link: entry {entry!r} names no screen"]
    seen = reachable(entry, screens)
    for sid, s in screens.items():
        out += _screen_problems(sid, s, seen, glossary, screens)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a UI inventory: reachable screens, working links, glossary terms.")
    ap.add_argument("inventory", help="path of the inventory JSON file")
    args = ap.parse_args(argv)
    inv, err = load(args.inventory)
    if err is not None or inv is None:
        print(err)
        return 1
    probs = problems(inv)
    for p in probs:
        print(p)
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
