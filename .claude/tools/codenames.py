"""code_name / hit_file: plain string parsing for any language, no regex.

code_name(tok): the identifier inside a quoted/backticked token. `ns::Class::method()` (C++/Rust), `obj.method` (Python/JS), `pkg.Func` (Go),
`Klass#meth`, `vec!`, `Foo<T>`, `~Dtor`, `$var` -> the last name component. Paths, flags and anything not one identifier -> None.
hit_file(line): the file of an `lp` hit line (`path:12-30  kind`, `path:12:3  text`), else None."""


def _drop_generics(t):
    out, depth = [], 0
    for ch in t:
        depth += ch == "<"
        if not depth:
            out.append(ch)
        depth -= ch == ">" and depth > 0
    return "".join(out)


def code_name(tok):
    t = tok.strip().strip("`'\"")
    if not t or "/" in t or t.startswith("-") or " " in t:
        return None
    t = _drop_generics(t).split("(")[0].rstrip("!?")
    for sep in ("::", "->", "#", "\\"):
        t = t.replace(sep, ".")
    last = [p for p in t.split(".") if p][-1:]
    n = last[0].lstrip("~$@&*") if last else ""
    return n if n.isidentifier() else None


def hit_file(line):
    """The file of an `lp` hit line: `path:12-30  kind`, `path:12  kind` or `path:12:3  text`; else None."""
    words = line.split(None, 1)
    if not words:
        return None
    parts = words[0].split(":")
    n = len(parts)
    while n > 1 and parts[n - 1].split("-")[0].isdigit():
        n -= 1
    return ":".join(parts[:n]) if n < len(parts) and n > 0 and parts[0] else None
