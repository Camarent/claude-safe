"""leads_docs: exact, no-model file digests for the planner context: top lines, doc cites, tests of the files, groups. Used by plan_context and pipeline/phase_context."""

import contextlib
import json
import os
import re
from pathlib import Path

from pipeline.configuration import WORK_DIR

from search.leadparts import is_test

DESCRIBED = 25  # files with a description and a doc cite; the rest are bare paths
DOC_GLOBS = ["CONTRACT.md", "AUDIT.md", "README.md", "ARCHITECTURE.md", "docs/*.md", "docs/modules/*.md"]


def _py_top(t):
    m = re.match(r'\s*(?:#!.*\n)?\s*(?:#[^\n]*\n)*\s*[rRbB]?("""|\'\'\')(.*?)\1', t, re.S)
    if m and m.group(2).strip():
        return m.group(2).strip().splitlines()[0][:110]
    m = re.search(r"def (test_\w+)", t)
    return "first test " + m.group(1) if m else ""


def _script_top(t):
    m = re.match(r"\s*(?:#!.*\n)?\s*(?:/\*\*?(.*?)\*/|(?:(?://|#)[^\n]*\n?)+)", t, re.S)
    if m:
        x = (m.group(1) or m.group(0)).strip("/*#\n ").splitlines()[0].strip("/*# ")
        if x:
            return x[:110]
    m = re.search(r"(?:test|describe|it)\(\s*['\"]([\w][^'\"\\\\]{3,})", t)
    return "first test: " + m.group(1)[:90] if m else ""


def _json_top(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # unreadable or malformed config file: no summary line, which is a normal state
        return ""
    if isinstance(d, dict):
        return "keys: " + ", ".join(list(d)[:8]) if len(d) <= 12 else f"object, {len(d)} entries"
    return f"list of {len(d)}" if isinstance(d, list) else ""


def top_line(root, f):
    """First line of the file's top comment or docstring, else the first test title, else JSON top-level keys; "" when none."""
    try:
        with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
            t = fh.read(4000)
    except OSError:  # a lead file that cannot be read gives no top line: normal, not a bug
        return ""
    if f.endswith(".py"):
        return _py_top(t)
    t = re.sub(r"\A\s*#!.*\n", "", t)
    if f.endswith((".js", ".sh")):
        return _script_top(t)
    if f.endswith(".md"):
        m = re.match(r"\s*#\s*(.+)", t)
        return m.group(1)[:110] if m else ""
    if f.endswith(".json"):
        return _json_top(os.path.join(root, f))
    return ""


def doc_mentions(root, files, per_file=2):
    """{file: ["doc:line: text", ...]}: lines of the doc files that name the file's basename."""
    import glob

    docs = sorted({d for g in DOC_GLOBS for d in glob.glob(os.path.join(root, g))})
    lines = {}
    for d in docs:
        with contextlib.suppress(OSError), open(d, encoding="utf-8", errors="ignore") as fh:
            lines[os.path.relpath(d, root)] = fh.read().splitlines()
    out: dict = {}
    for f in files:
        base = os.path.basename(f)
        for d, ls_ in lines.items():
            if d == f:
                continue
            for n, line in enumerate(ls_, 1):
                if base in line and len(out.setdefault(f, [])) < per_file:
                    out[f].append(f"{d}:{n}: " + " ".join(line.split())[:140])
    return out


def with_docs(root, files, cap=6):
    """FILES plus up to CAP doc files (DOC_GLOBS) that name a basename of FILES, most-citing first. Docs already in FILES are not repeated."""
    cites: dict = {}
    for hits in doc_mentions(root, [f for f in files if group_of(f) != "docs"], per_file=2).values():
        for h in hits:
            d = h.split(":", 1)[0]
            cites[d] = cites.get(d, 0) + 1
    extra = [d for d, _ in sorted(cites.items(), key=lambda kv: -kv[1]) if d not in files][:cap]
    return list(files) + extra


_WALK_SKIP = {".git", "node_modules", "target", "dist", ".cache", ".code-index"}


def _test_stem(f):
    """Basename stem of a test file with `test_` prefix and `.test`/`_test` infix or suffix removed."""
    stem = os.path.splitext(os.path.basename(f))[0]
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    for sep in (".test", "_test"):
        if stem.endswith(sep):
            stem = stem[: -len(sep)]
    return stem


def _walk_tests(root):
    """Repo-relative paths (posix) of every test file under ROOT, skipping vendor and cache dirs."""
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _WALK_SKIP and not d.startswith(WORK_DIR)]
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/")
            if is_test(rel):
                out.append(rel)
    return sorted(out)


REF_READ_CAP = 200_000  # bytes of one test file read when looking for names of source files


def _refs_in(text, src):
    """True when TEXT names the source file SRC: its basename with extension, or its stem as the end of a quoted/slashed path (import, fs read). Stems under 4 characters never match."""
    base = os.path.basename(src)
    stem = os.path.splitext(base)[0]
    if base in text:
        return len(stem) >= 4
    return len(stem) >= 4 and re.search(r"[/'\"]" + re.escape(stem) + r"""(?:\.\w+)?['"]""", text) is not None


def with_tests(root, files, cap=12, per_src=4):
    """FILES plus up to CAP test files that belong to the non-test, non-doc files of FILES: PER_SRC per source file, first by same basename stem
    (test_x.py, x.test.js), then by a test file that names the source (import or path read; this reaches tests whose name differs).
    Same directory subtree first, then shortest path. Tests already in FILES are not repeated."""
    have = set(files)
    tests = [t for t in _walk_tests(root) if t not in have]
    texts = {}

    def text_of(t):
        if t not in texts:
            try:
                with open(os.path.join(root, t), encoding="utf-8", errors="replace") as fh:
                    texts[t] = fh.read(REF_READ_CAP)
            except OSError:
                texts[t] = ""
        return texts[t]

    extra: list = []
    for src in files:
        if group_of(src) in ("tests", "docs") or len(extra) >= cap:
            continue
        for t in _tests_for_src(src, tests, extra, text_of, per_src):
            if len(extra) >= cap:
                break
            extra.append(t)
    return list(files) + extra


def _tests_for_src(src, tests, extra, text_of, per_src):
    """Up to PER_SRC tests of TESTS not in EXTRA for source SRC: same stem first, then tests that name SRC; same subtree, then shortest."""
    stem = os.path.splitext(os.path.basename(src))[0]
    top = os.path.dirname(src)
    order = lambda t, top=top: (0 if not top or t.startswith(top + "/") else 1, len(t), t)  # noqa: E731
    hits = sorted((t for t in tests if _test_stem(t) == stem and t not in extra), key=order)
    refs = sorted((t for t in tests if t not in extra and t not in hits and _refs_in(text_of(t), src)), key=order)
    return (hits + refs)[:per_src]


def group_of(f):
    if is_test(f):
        return "tests"
    if f.endswith((".py", ".js", ".rs", ".ts", ".sh")):
        return "code"
    return "docs" if f.endswith(".md") else "data"


def _code_lines(root, code):
    """The `### code` group: the first DESCRIBED files with top line and one doc cite (cites that name 3+ files are dropped), the rest bare."""
    men = doc_mentions(root, code[:DESCRIBED], per_file=1)
    seen: dict = {}
    for ms in men.values():
        for m in ms:
            seen[m] = seen.get(m, 0) + 1
    res = []
    for k, f in enumerate(code):
        if k >= DESCRIBED:
            res.append(f"- {f}")
            continue
        t = top_line(root, f)[:80]
        res.append(f"- {f}" + (f" - {t}" if t else "") + "".join(f"\n    {m[:110]}" for m in men.get(f, []) if seen[m] <= 2))
    return res


def _test_lines(tests):
    """Test files collapsed per directory: `- dir/: name, name`."""
    per_dir: dict = {}
    for f in tests:
        per_dir.setdefault(os.path.dirname(f) or ".", []).append(os.path.basename(f))
    return ["### tests"] + [f"- {d}/: " + ", ".join(n) for d, n in per_dir.items()]


def _capped_lines(root, group, files, cap):
    """Files of one group: the first `cap` with their top line, the rest as bare paths."""
    res = [f"### {group}"]
    res += [f"- {f}" + (f" - {top_line(root, f)[:80]}" if top_line(root, f) else "") for f in files[:cap]]
    res += [f"- {f}" for f in files[cap:]]
    return res


def digest(root, files):
    """Planner-facing lines in groups (code, tests, docs, data), each ranked as FILES is. All facts are copied from the files:
    code gets top comment and one doc cite, tests collapse per directory, docs show their title, data its keys."""
    by = {g: [f for f in files if group_of(f) == g] for g in ("code", "tests", "docs", "data")}
    res = ["### code (most-named first)", *_code_lines(root, by["code"])] if by["code"] else []
    if by["tests"]:
        res += _test_lines(by["tests"])
    for g in ("docs", "data"):
        if by[g]:
            res += _capped_lines(root, g, by[g], 8)
    return res


def code_files(ctx_text):
    """Paths of the `### code` group of a context file, in order."""
    out, on = [], False
    for line in ctx_text.splitlines():
        if line.startswith("###"):
            on = line.startswith("### code")
        elif on and line.startswith("- "):
            out.append(line[2:].split(" - ")[0].strip())
    return out
