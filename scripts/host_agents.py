"""Render host-ready copies of the pipeline agents (`.claude/agents/*.md`), which are written for the container.

Changes per agent: name gets a prefix (`dev` -> `pl-dev`), tools that do not exist on the host are dropped from the `tools:` line, every
`lp` instruction line is replaced by one Grep/Glob line, `/root/.claude` and `/workspace` take their host values from paths.json, and each `cs-*` name becomes the absolute path of its wrapper.
Dry run by default; `--apply` writes the files.

    python3 -I scripts/host_agents.py [--home DIR] [--out DIR] [--prefix pl-] [--apply]
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claude", "tools"))
from pipeline import cmdindex  # the sys.path line above must run first

DROP_TOOLS = ("mcp__chrome-devtools__", "Artifact", "WebFetch")  # not available in a host session
NAV_LINE = "Code navigation: use Grep and Glob, then Read only the slice you need. (`lp` is a container tool and is not installed on this host.)"
LP_LINE = re.compile(r"^(Code navigation:|Locating code:|Unknown location:|For `lp )")
CONTAINER_HOME = "/root/.claude"
# inline `lp` mentions inside prose: exact phrase -> host text (applied per line, before the catch-all)
INLINE_LP = (
    ("(`lp nav refs FILE SYMBOL`, exact; grep only to confirm literal text)", "(Grep for the symbol; read the matches)"),
    ("a script made it with `lp`", "a script made it"),
    ("Bash is for `lp` and read-only commands only", "Bash is for read-only commands only"),
    ("Bash is for `git diff`, `git show`, `lp` and read-only commands only", "Bash is for `git diff`, `git show` and read-only commands only"),
)
LP_SPAN = re.compile(r"`lp [^`]*`")  # catch-all: any other `lp <subcommand> ...` code span
LP_SPAN_HOST = "Grep/Glob"


def split_frontmatter(text):
    """-> (frontmatter lines without the --- fences, body text); (None, text) when there is no frontmatter."""
    lines = text.split("\n")
    if lines and lines[0] == "---" and "---" in lines[1:]:
        end = lines.index("---", 1)
        return lines[1:end], "\n".join(lines[end + 1 :])
    return None, text


def render_frontmatter(fm, prefix):
    out, dropped = [], []
    for src in fm:
        line = src
        if line.startswith("name:"):
            line = f"name: {prefix}{line[5:].strip()}"
        elif line.startswith("tools:"):
            tools = [t.strip() for t in src[6:].split(",") if t.strip()]
            dropped = [t for t in tools if t.startswith(DROP_TOOLS)]
            line = "tools: " + ", ".join(t for t in tools if not t.startswith(DROP_TOOLS))
        out.append(line)
    return out, dropped


class IndexError_(ValueError):
    """An agent names a cs-* command the index or the wrapper folder does not provide."""


def wrapper_path(index, name, home):
    """Absolute wrapper path of the host command `name`; raises IndexError_ when unknown or the wrapper file is missing."""
    host = cmdindex.commands(index, "host")
    for key in host:
        if cmdindex.command_name(index, key) == name:
            folder = cmdindex.BIN_DIRS["host" if cmdindex.side_of(host[key]) == "host" else "container"]
            path = os.path.join(home, folder, name)
            if not os.path.isfile(path):
                raise IndexError_(f"wrapper missing for {name}: {path} (run tools/cmdindex.py gen-bin)")
            return path
    raise IndexError_(f"{name} is not a host command in {cmdindex.INDEX_NAME}")


def replace_commands(text, index, home):
    """-> (text with every bare cs-* name replaced by its absolute wrapper path, count)."""
    pat = re.compile(r"(?<![\w/.-])" + re.escape(index["prefix"]) + r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")
    names = pat.findall(text)
    return pat.sub(lambda m: wrapper_path(index, m.group(0), home), text), len(names)


def render_body(body, home, index=None):
    """-> (new body, number of lp lines removed, number of home paths replaced, number of commands replaced).
    Without an index only the container home is replaced (no command names)."""
    out, removed, placed = [], 0, False
    for line in body.split("\n"):
        if LP_LINE.match(line):
            removed += 1
            if not placed:
                out.append(NAV_LINE)
                placed = True
            continue
        text_line = line
        for old, new in INLINE_LP:
            text_line = text_line.replace(old, new)
        out.append(LP_SPAN.sub(LP_SPAN_HOST, text_line))  # the catch-all runs after the exact phrases
    text = "\n".join(out)
    if index is None:
        return text.replace(CONTAINER_HOME, home), removed, text.count(CONTAINER_HOME), 0
    # root swap first: the wrappers it leaves behind (`<home>/bin/cs-x`) are then not rescanned by the root swap
    text, paths = cmdindex.swap(text, "host", index, home)
    text, cmds = replace_commands(text, index, home)
    return text, removed, paths, cmds


def render(text, home, prefix, index=None):
    """-> (rendered text, report dict)."""
    fm, body = split_frontmatter(text)
    fm2, dropped = render_frontmatter(fm or [], prefix)
    body2, removed, paths, cmds = render_body(body, home, index)
    if index is not None:  # frontmatter too (description names commands)
        fm2 = [replace_commands(x, index, home)[0] for x in fm2]
    head = "".join(f"{x}\n" for x in ["---", *fm2, "---"]) if fm is not None else ""
    return head + body2, {"dropped_tools": dropped, "lp_lines": removed, "paths": paths, "commands": cmds}


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", default=os.path.join(os.path.dirname(here), ".claude"), help="pipeline home written into the agent text")
    ap.add_argument("--out", default=os.path.expanduser("~/.claude/agents"))
    ap.add_argument("--prefix", default="pl-")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    src = os.path.join(a.home, "agents")
    index = cmdindex.load(a.home)
    names = sorted(f for f in os.listdir(src) if f.endswith(".md"))
    print(f"{'APPLY' if a.apply else 'DRY RUN'}: {len(names)} agents {src} -> {a.out} (prefix {a.prefix!r}, home {a.home})")
    if a.apply:
        os.makedirs(a.out, exist_ok=True)
    for fn in names:
        with open(os.path.join(src, fn), encoding="utf-8") as f:
            try:
                new, rep = render(f.read(), a.home, a.prefix, index)
            except IndexError_ as e:
                print(f"host_agents: {fn}: {e}", file=sys.stderr)
                return 1
        print(f"  {a.prefix}{fn}: -{rep['lp_lines']} lp lines, {rep['paths']} paths, {rep['commands']} commands, dropped tools {rep['dropped_tools'] or '-'}")
        if a.apply:
            with open(os.path.join(a.out, a.prefix + fn), "w", encoding="utf-8") as f:
                f.write(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
