"""Gate mode of a gate command (`mode_of`) and of the modes a script header lists (`header_modes`).
Leaf module: reads only the regex and limits in pipeline.configuration; no I/O."""

import re

from .configuration import GATE_MODE_FLAG_RE, GATE_MODE_FULL, HEADER_LINES_MAX


def mode_of(cmd) -> str:
    """-> the first mode flag of the command CMD (GATE_MODE_FLAG_RE), else GATE_MODE_FULL."""
    m = re.search(GATE_MODE_FLAG_RE, cmd)
    return m.group(0) if m else GATE_MODE_FULL


def header_modes(text) -> set:
    """-> the mode flags named in the comment or docstring lines among the first HEADER_LINES_MAX lines of the script TEXT; set() when none."""
    return {m.group(0) for m in re.finditer(GATE_MODE_FLAG_RE, text_header(text))}


def subcommand_modes(text, sub) -> set:
    """-> the mode flags a script header lists in brackets right after the subcommand SUB (`gate [--ext|--pytest]`); set() when the header gives SUB none."""
    modes: set[str] = set()
    for m in re.finditer(rf"(?<![\w-]){re.escape(sub)}\s*\[([^\]]*)\]", text_header(text)):
        modes.update(f.group(0) for f in re.finditer(GATE_MODE_FLAG_RE, m.group(1)))
    return modes


def text_header(text) -> str:
    """-> the first HEADER_LINES_MAX lines of the script TEXT that are comment or docstring lines, joined by newlines."""
    keep: list[str] = []
    in_doc = False
    for line in text.splitlines()[:HEADER_LINES_MAX]:
        s = line.strip()
        quotes = s.count('"""')
        if in_doc or quotes or s.startswith("#"):
            keep.append(s)
        if quotes % 2:
            in_doc = not in_doc
    return "\n".join(keep)
