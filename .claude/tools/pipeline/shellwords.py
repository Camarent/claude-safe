"""Small shell-word helpers shared by the command guards."""

import re
import string

ALWAYS = ("\\|\\|", "&&", ";")  # separators every caller splits at
PIPE = "\\|"
AMP = "(?<![<>])&(?!>)"  # a single `&`, not a redirect like `2>&1`, `<&3` or `&>`
NEWLINE = "\\n"
SEPARATORS = {
    (pipe, amp, newline): re.compile("|".join([ALWAYS[0], *([PIPE] if pipe else []), *ALWAYS[1:], *([AMP] if amp else []), *([NEWLINE] if newline else [])]))
    for pipe in (False, True)
    for amp in (False, True)
    for newline in (False, True)
}


def split_commands(cmd, pipe=True, amp=True, newline=True):
    """Split a command line at `&&`, `||` and `;`, and also at a single `|` (PIPE), a single background `&` (AMP) and a newline (NEWLINE)."""
    return SEPARATORS[(pipe, amp, newline)].split(cmd)


def short_flag(arg, letters):
    """True when ARG is a short-option group (`-rnf`, one dash) holding any of LETTERS; `--long` options never match."""
    if not arg.startswith("-"):
        return False
    group = ""
    for c in arg[1:]:
        if c not in string.ascii_letters:
            break
        group += c
    return any(c in letters for c in group)


def starts_command(cmd, names):
    """True when a command segment of CMD (split at `&&`, `||`, `;`, `|`, `&`; not at a newline) begins with one of NAMES followed by a blank."""
    for seg in split_commands(cmd, newline=False):
        word = seg.lstrip().split(None, 1)
        if word and word[0] in names and seg.lstrip()[len(word[0]) :][:1].isspace():
            return True
    return False


HEREDOC_OPEN = re.compile(r"(<<-?\s*['\"]?(\w+)['\"]?)([^\n]*)\n")


def strip_heredocs(cmd, keep_opener=True):
    """Drop heredoc bodies and the terminator line: `<<WORD` (also `<<-`, quoted WORD) up to the first line that is WORD. An unterminated one stays. KEEP_OPENER False drops the whole heredoc (opener line rest included) for a space."""
    out: list[str] = []
    pos = at = 0
    dead = set()  # words with no terminator line: none after this point has one either
    while True:
        m = HEREDOC_OPEN.search(cmd, at)
        if not m:
            return "".join(out) + cmd[pos:]
        at = m.start() + 1
        end = -1 if m.group(2) in dead else heredoc_end(cmd, m.end(), m.group(2))
        if end < 0:
            dead.add(m.group(2))
            continue
        out.append(cmd[pos : m.start()] + (m.group(1) + m.group(3) if keep_opener else " "))
        pos = at = end


def heredoc_end(cmd, start, word):
    """-> index just after the first line from `start` that is `word` (blanks around it allowed), or -1."""
    while start <= len(cmd):
        eol = cmd.find("\n", start)
        stop = len(cmd) if eol < 0 else eol
        if cmd[start:stop].strip(" \t") == word:
            return stop
        if eol < 0:
            return -1
        start = eol + 1
    return -1
