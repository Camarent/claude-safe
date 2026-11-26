#!/usr/bin/env python3
"""Copy the body of skills/caveman/SKILL.md into every agent except SKIP (between the caveman markers).
   python3 sync_caveman.py          # rewrite agents/*.md
   python3 sync_caveman.py --check  # write nothing; exit 1 and list the agents that are out of date
Agent frontmatter `skills:` does not preload skills here, so the text is inlined; edit the skill, then run this."""

import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {"docs-writer", "publisher"}  # their output is text a person reads or ships
AGENTS = sorted(f[:-3] for f in os.listdir(f"{ROOT}/agents") if f.endswith(".md") and f[:-3] not in SKIP)
B, E = "<!-- caveman:begin -->", "<!-- caveman:end -->"
skill = Path(f"{ROOT}/skills/caveman/SKILL.md").read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
skill = re.sub(r"<!--.*?-->\n?", "", skill, flags=re.S)
# chat-only parts are dead weight for subagents: keep Rules 1-7 and the "When to break" exceptions (upstream: security, irreversible, confusion get full sentences)
DROP = ("## Persistence", "## Why", "### 8.", "### 9.", "## Pre-send check")
parts = re.split(r"\n(?=##+ )", skill)
skill = "\n".join(x for x in parts if not x.startswith(DROP)).strip()
guard = (
    "Style: your own prose and your reply use the terse voice below. Code, paths, commands, numbers, `path:line` cites and everything "
    "you write into files stay exact; only filler dies. Markers like `ESCALATE:` keep their exact form."
)
block = f"{B}\n{guard}\n\n{skill}\n{E}"
check = "--check" in sys.argv[1:]
stale = []
for a in AGENTS:
    p = f"{ROOT}/agents/{a}.md"
    s = Path(p).read_text(encoding="utf-8")
    s = re.sub(r"\nskills:\n  - caveman", "", s, count=1)
    s = re.sub(r"\n*Style: terse caveman voice.*?\n", "\n", s, count=1)
    s = re.sub(re.escape(B) + r".*?" + re.escape(E), lambda m: block, s, flags=re.S) if B in s else s.rstrip("\n") + "\n\n" + block + "\n"
    if check:
        if s != Path(p).read_text(encoding="utf-8"):
            stale.append(a)
        continue
    Path(p + ".tmp").write_text(s, encoding="utf-8")
    os.replace(p + ".tmp", p)
    print(a, len(s.split()), "words")  # replace, not rewrite: some files are root-owned from container edits
if stale:
    print("out of date (run sync_caveman.py): " + ", ".join(stale))
    sys.exit(1)
