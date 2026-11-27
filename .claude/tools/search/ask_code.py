#!/usr/bin/env python3
"""Fuzzy code questions for agents: a local model steers a deterministic index. It never browses the repo itself.

  ask_code.py "where do we cap new items per day?" [--root DIR] [--no-model] [--careful] [--json] [--reindex] | --index [--reindex]
  --careful   model reasons before judging: ~33 s instead of ~7 s, fewer false hits. No local model reachable: falls back to --no-model.

Pipeline: (1) model turns the question into symbol names + keywords (skipped with --no-model), (2) code looks them up in
.code-index/ (ctags symbols + BM25 over 30-line chunks, identifiers split into words), (3) model judges the top candidates,
(4) answer = file:line citations, or IDK. Max 2 rounds. Exact questions ("who calls X") belong to lsp.py, not here.

Index: .code-index/index.db under ROOT. Refreshed on every call by file mtime+size (only changed files are re-parsed, deleted
files dropped); --reindex rebuilds from scratch. Add .code-index/ to .gitignore.
Needs the Strata server (run.sh --with strata) unless --no-model."""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from itertools import zip_longest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import HOME, WORK_DIR, tool_setting
from pipeline.util import swallowed

SKIP_DIRS = {
    "node_modules",
    ".git",
    "target",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    ".code-index",
    "vendor",
    "cargo-target",
    WORK_DIR,
    ".audits",
    ".worktrees",
}
KEEP_DOT_DIRS = {".github", ".notes", ".githooks"}  # dot-folders still indexed; every other dot-folder (.cache, .wt, .ruff_cache, ...) is skipped
NOISE = ("/tests/", "/test/", "tests/", "docs/", ".md", "/i18n/", ".json", "/snapshots/", ".css")  # real source should outrank these
EXTS = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".rs", ".md", ".json", ".html", ".css", ".toml", ".sh"}
CHUNK = 30
STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "be",
    "by",
    "as",
    "at",
    "from",
    "that",
    "this",
    "it",
    "its",
    "if",
    "when",
    "then",
    "than",
    "not",
    "no",
    "into",
    "per",
    "each",
    "any",
    "all",
    "only",
    "has",
    "have",
    "do",
    "does",
    "how",
    "what",
    "where",
    "which",
    "who",
    "why",
    "we",
    "our",
    "code",
    "used",
    "use",
}


def find_root(start=None):
    """Nearest ancestor of start (default cwd) with .git (dir or worktree file); else nearest with .code-index; else start."""
    start = os.path.abspath(start or os.getcwd())
    for marker in (lambda c: os.path.exists(os.path.join(c, ".git")), lambda c: os.path.isdir(os.path.join(c, ".code-index"))):
        cur = start
        while True:
            if marker(cur):
                return cur
            up = os.path.dirname(cur)
            if up == cur:
                break
            cur = up
    return start


def words(s):
    """identifier text -> lowercase words: refreshQueue -> refresh queue, daily_cap -> daily cap"""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w and w not in STOP]


class _PerThread:
    """dict-like, one copy per thread: the background server answers several requests at once and their diagnostics must not mix."""

    def __init__(self):
        self._t = threading.local()

    @property
    def d(self):
        if not hasattr(self._t, "d"):
            self._t.d = {}
        return self._t.d

    def __getitem__(self, k):
        return self.d[k]

    def __setitem__(self, k, v):
        self.d[k] = v

    def get(self, k, default=None):
        return self.d.get(k, default)

    def setdefault(self, k, default=None):
        return self.d.setdefault(k, default)

    def clear(self):
        self.d.clear()


LAST = _PerThread()  # last call's diagnostics: candidates judged, chunks still to embed
SCHEMA = 3  # bump when tables change: the index then rebuilds itself once
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".rs"}
FUNC_KINDS = {"function", "method", "class", "struct", "interface", "enum", "module", "namespace", "trait", "implementation", "generator", "union", "member"}
COMMENT = ("//", "#", "/*", "*", "*/", "///", "--")
GENERIC = {"get", "set", "is", "has", "init", "make", "run", "do", "on", "to", "from", "with", "new_", "add"}


def connect(root, fresh=False):
    root = os.path.abspath(root)
    d = os.path.join(root, ".code-index")
    os.makedirs(d, exist_ok=True)
    db = os.path.join(d, "index.db")
    if not os.path.exists(os.path.join(d, ".gitignore")):
        Path(os.path.join(d, ".gitignore")).write_text("*\n", encoding="utf-8")  # index never gets committed, in any project
    if fresh and os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    if con.execute("pragma user_version").fetchone()[0] != SCHEMA:
        for t in ("files", "symbols", "spans", "chunks", "vecs"):
            con.execute(f"drop table if exists {t}")
        con.execute(f"pragma user_version={SCHEMA}")
    con.executescript("""create table if not exists files(path text primary key, mtime real, size int, sha text);
        create table if not exists symbols(path text, line int, kind text, name text, scope text);
        create index if not exists sym_name on symbols(name);
        create table if not exists spans(id integer primary key, path text, start int, stop int);
        create index if not exists span_path on spans(path, start);
        create table if not exists vecs(id integer primary key, v blob);
        create virtual table if not exists chunks using fts5(body, tokenize='porter unicode61');""")
    return con


def source_files(root):
    for d, ds, ns in os.walk(root):
        ds[:] = [x for x in ds if x not in SKIP_DIRS and (not x.startswith(".") or x in KEEP_DOT_DIRS)]  # the walk root (d == root) is never pruned
        for n in ns:
            if os.path.splitext(n)[1].lower() in EXTS and not n.endswith(".min.js"):
                yield os.path.relpath(os.path.join(d, n), root)


def _leading_comment_start(lines, ln):
    """First line of the comment block (at most 12 lines) directly above symbol line `ln`, else `ln`."""
    st, up = ln, 0
    while st > 1 and up < 12 and lines[st - 2].strip().startswith(COMMENT):
        st -= 1
        up += 1
    return st


def _append_piece(out, piece):
    """Add a 40-line-capped piece to `out`; a sliver under 6 lines joins the previous chunk when that stays within 40 lines."""
    if out and piece[1] - piece[0] + 1 < 6 and piece[1] - out[-1][0] + 1 <= 40:
        out[-1] = (out[-1][0], piece[1])  # sliver: join the previous chunk
    else:
        out.append(piece)


def spans_for(lines, symlines):
    """Code files: one chunk per function/class (its leading comment included), tiling the file; pieces over 40 lines are split.
    Other files, or code with no symbols: fixed 30-line windows. Chunks under 6 lines join the previous one."""
    n = len(lines)
    if not symlines:
        return [(i + 1, min(i + CHUNK, n)) for i in range(0, n, CHUNK)]
    starts = sorted({_leading_comment_start(lines, ln) for ln in symlines})
    bounds = ([1] if starts[0] > 1 else []) + starts + [n + 1]
    out: list = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        for x in range(a, b, 40):
            _append_piece(out, (x, min(x + 39, b - 1)))
    return out


EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_CHARS = 700  # head of a chunk is enough: path + signature + comment
_emb: dict = {}


def embedder():
    """fastembed model (CPU, ONNX) or None when the library is not installed: search then works on keywords and symbols alone."""
    if "m" not in _emb:
        try:
            from fastembed import TextEmbedding

            _emb["m"] = TextEmbedding(EMBED_MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH", "/opt/fastembed-cache"))
        except ImportError:  # the library is optional
            _emb["m"] = None
        except Exception as ex:  # model files missing or unreadable: keyword search only, but say so
            swallowed("ask_code.embedder", ex)
            _emb["m"] = None
    return _emb["m"]


def cache_path(root=None):
    """Where the shared vector cache lives: CODE_INDEX_CACHE, else <workspace>/.code-index/vectors.db (workspace = nearest folder at or above ROOT holding pipeline.json:
    the project mount, so it survives a container restart and every worktree and nested repo of the project shares it), else ~/.code-cache/vectors.db."""
    if os.environ.get("CODE_INDEX_CACHE"):
        return os.environ["CODE_INDEX_CACHE"]
    d = os.path.abspath(root) if root else ""
    while d:
        if os.path.exists(os.path.join(d, "pipeline.json")):
            return os.path.join(d, ".code-index", "vectors.db")
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.join(HOME, ".code-cache", "vectors.db")


def vcache(root=None):
    """Cache of chunk vectors keyed by hash(model + text), shared by all checkouts of a project: a fresh worktree or clone re-embeds nothing it has seen. None if unwritable."""
    path = cache_path(root)
    try:
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        if os.path.basename(d) == ".code-index" and not os.path.exists(os.path.join(d, ".gitignore")):
            Path(d, ".gitignore").write_text("*\n", encoding="utf-8")  # never committed
        c = sqlite3.connect(path, timeout=tool_setting("ASK_DB_TIMEOUT_S"))
        c.execute("pragma journal_mode=wal")
        c.execute("create table if not exists vec(h text primary key, v blob)")
        return c
    except (OSError, sqlite3.Error) as ex:
        swallowed("ask_code.vcache", ex)  # cache is optional: the caller runs without it
        return None


def _pending_items(con, root):
    """[(span id, text to embed, cache key)] for every chunk that has no vector yet and whose file can be read."""
    pend = con.execute(
        "select s.id, s.path, s.start, s.stop from spans s left join vecs v on v.id = s.id where v.id is null order by s.path, s.start"
    ).fetchall()
    lines: dict[str, list[str] | None] = {}
    items: list = []
    for i, path, a, b in pend:
        if path not in lines:
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                    lines[path] = fh.read().split("\n")
            except OSError:
                lines[path] = None
        got = lines[path]
        if got is None:
            continue  # unreadable file: no vector rather than one cached under the path text
        text = f"{path}\n" + "\n".join(got[a - 1 : b])[:EMBED_CHARS]
        items.append((i, text, hashlib.sha1((EMBED_MODEL + "\n" + text).encode(), usedforsecurity=False).hexdigest()))
    return items


def _cached_vectors(cc, items):
    """{cache key: blob} for the items whose text the shared cache already holds."""
    hit = {}
    if cc is not None:
        for k in range(0, len(items), 500):
            chunk = [h for _, _, h in items[k : k + 500]]
            hit.update(cc.execute("select h, v from vec where h in ({})".format(",".join("?" * len(chunk))), chunk).fetchall())  # nosec  # only ? placeholders are formatted in
    return hit


def embed_pending(con, root, budget=None):
    """Give every chunk a vector: from the shared cache when its text was embedded before, else by the model (at most `budget` model calls; None = all).
    Returns (done, still_pending)."""
    m = embedder()
    if m is None:
        return 0, 0
    import numpy as np

    items = _pending_items(con, root)
    if not items:
        return 0, 0
    cc = vcache(root)
    hit = _cached_vectors(cc, items)
    for i, _, h in items:
        if h in hit:
            con.execute("insert or replace into vecs values (?,?)", (i, hit[h]))
    miss = [it for it in items if it[2] not in hit]
    todo = miss[:budget] if budget else miss
    for (i, _, h), v in zip(todo, m.embed([t for _, t, _ in todo], batch_size=64), strict=False):
        blob = np.asarray(v, dtype="float32").tobytes()
        con.execute("insert or replace into vecs values (?,?)", (i, blob))
        if cc is not None:
            cc.execute("insert or replace into vec values (?,?)", (h, blob))
    if cc is not None:
        cc.commit()
        cc.close()
    con.commit()
    return len(todo), len(miss) - len(todo)


def vector_ranked(con, question, n=80):
    """Span ids by cosine similarity to the question (empty when no embedder or no vectors yet)."""
    m = embedder()
    if m is None:
        return []
    import numpy as np

    rows = con.execute("select id, v from vecs").fetchall()
    if not rows:
        return []
    M = np.frombuffer(b"".join(r[1] for r in rows), dtype="float32").reshape(len(rows), -1)
    q = np.asarray(next(iter(m.query_embed([question]))), dtype="float32")
    return [rows[j][0] for j in np.argsort(-(M @ q))[:n]]


def refresh(root, fresh=False, embed=None, wait=True):
    """Incremental: re-parse files whose content changed, drop deleted ones, embed new chunks. Returns (changed, removed).
    One writer per project at a time (file lock). wait=False: if another process is indexing right now, skip and use the index as it is."""
    import fcntl

    root = os.path.abspath(root)
    os.makedirs(os.path.join(root, ".code-index"), exist_ok=True)
    with open(os.path.join(root, ".code-index", "lock"), "w", encoding="utf-8") as lock:
        got = False
        for _ in range(1 if wait else 30):  # no-wait callers still give a short refresh by someone else 3 s to finish
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
                got = True
                break
            except OSError:
                time.sleep(0.1)
        if not got:
            LAST["pending"] = 0
            LAST["busy"] = True
            return 0, 0
        return _refresh(root, fresh, embed)


def _scan_files(con, root, known):
    """-> (now, changed, shas): current (mtime, size) of every source file, the ones whose content changed, their hashes.
    A file only touched (same hash) just gets its mtime/size updated."""
    now, changed, shas = {}, [], {}
    for rel in source_files(root):
        try:
            st = os.stat(os.path.join(root, rel))
        except OSError:  # broken symlink or file removed mid-scan: skip it
            continue
        now[rel] = (st.st_mtime, st.st_size)
        k = known.get(rel)
        if k and (k[0], k[1]) == now[rel] or st.st_size >= 1_000_000:
            continue
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                shas[rel] = hashlib.sha1(fh.read(), usedforsecurity=False).hexdigest()
        except OSError:  # file removed or unreadable mid-scan: nothing to index, skip it
            continue
        if k and k[2] == shas[rel]:  # touched or checked out again, content identical: no re-parse
            con.execute("update files set mtime=?, size=? where path=?", (*now[rel], rel))
        else:
            changed.append(rel)
    return now, changed, shas


def _drop_files(con, paths):
    for p in paths:
        con.execute("delete from chunks where rowid in (select id from spans where path=?)", (p,))
        con.execute("delete from vecs where id in (select id from spans where path=?)", (p,))
        for t in ("files", "symbols", "spans"):
            con.execute(f"delete from {t} where path=?", (p,))  # nosec  # table name comes from the fixed tuple above


def _store_symbols(con, root, changed):
    """Run ctags on the changed code files, store their symbols; -> {path: {lines where a function/class starts}}."""
    code = [p for p in changed if os.path.splitext(p)[1].lower() in CODE_EXT]
    symlines: dict = {}
    if not code:
        return symlines
    try:
        r = subprocess.run(
            ["ctags", "--output-format=json", "--fields=+nKS", "-f", "-", "-L", "-"],
            input="\n".join(os.path.join(root, p) for p in code),
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
    except OSError:  # ctags not installed: search works without symbols
        return symlines
    for line in r.stdout.splitlines():
        try:
            t = json.loads(line)
        except ValueError:  # one malformed ctags line: skip that tag only
            continue
        if t["name"].startswith("Anonymous"):
            continue  # ctags names every callback; they only fragment chunks and pollute the vocabulary
        rel = os.path.relpath(t["path"], root)
        con.execute("insert into symbols values (?,?,?,?,?)", (rel, t["line"], t.get("kind", ""), t["name"], t.get("scope", "")))
        if t.get("kind") in FUNC_KINDS:
            symlines.setdefault(rel, set()).add(t["line"])
    return symlines


def _store_chunks(con, root, rel, symlines):
    """Chunk one file into spans + the full-text table; False when it cannot be read."""
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError as ex:
        swallowed("ask_code.store_chunks", ex)  # the file stays unindexed; the scan goes on
        return False
    pathwords = " ".join(words(rel))
    for a, b in spans_for(lines, symlines.get(rel)):
        body = "\n".join(lines[a - 1 : b])
        if not body.strip():
            continue
        sid = con.execute("insert into spans(path, start, stop) values (?,?,?)", (rel, a, b)).lastrowid
        con.execute(
            "insert into chunks(rowid, body) values (?,?)",
            (sid, body + "\n" + pathwords + " " + " ".join(w for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", body) for w in words(t))),
        )
    return True


def _refresh(root, fresh, embed):
    con = connect(root, fresh)
    known = {p: (m, s, h) for p, m, s, h in con.execute("select path, mtime, size, sha from files")}
    now, changed, shas = _scan_files(con, root, known)
    gone = [p for p in known if p not in now]
    _drop_files(con, changed + gone)
    symlines = _store_symbols(con, root, changed)
    for rel in changed:
        if _store_chunks(con, root, rel, symlines):
            con.execute("insert into files values (?,?,?,?)", (rel, *now[rel], shas[rel]))
    con.commit()
    LAST["pending"] = embed_pending(con, root, embed)[1] if embed != 0 else 0  # embed: None = everything now, N = at most N chunks, 0 = skip
    con.close()
    return len(changed), len(gone)


def vocabulary(root, n=100, nfiles=60):
    """The words this codebase uses in function names and file names, most common first: grounds the model's guesses in real names."""
    from collections import Counter

    con = connect(root)
    names = Counter(
        w
        for (nm,) in con.execute("select name from symbols where kind in ({})".format(",".join("?" * len(FUNC_KINDS))), tuple(FUNC_KINDS))  # nosec  # only ? placeholders are formatted in
        for w in words(nm)
        if len(w) > 2 and w not in GENERIC
    )
    stems = Counter(
        w
        for (p,) in con.execute("select path from files")
        if not any(x in p for x in NOISE)
        for w in words(os.path.splitext(os.path.basename(p))[0])
        if len(w) > 2
    )
    con.close()
    return [w for w, _ in names.most_common(n)], [w for w, _ in stems.most_common(nfiles)]


def seen_names(root, cands, n=25):
    con = connect(root)
    out = []
    for c in cands:
        out += [nm for (nm,) in con.execute("select name from symbols where path=? and line between ? and ?", (c["path"], c["start"], c["end"]))]
    con.close()
    return list(dict.fromkeys(out))[:n]


def _bm25(con, tokens, n=40):
    if not tokens:
        return []
    q = " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens))
    return [r[0] for r in con.execute("select rowid from chunks where chunks match ? order by bm25(chunks) limit ?", (q, n))]


def _symbol_hits(con, symbols):
    """[(span id, score, why)]: exact symbol name 100, partial 40."""
    out = []
    for sy in symbols:
        for path, line, kind, name in con.execute(
            "select path, line, kind, name from symbols where lower(name)=lower(?) or lower(name) like ? limit 20", (sy, f"%{sy.lower()}%")
        ):
            row = con.execute("select id from spans where path=? and start<=? and stop>=? limit 1", (path, line, line)).fetchone()
            if row:
                out.append((row[0], 100 if name.lower() == sy.lower() else 40, f"symbol {kind} {name}"))
    return out


def _interleave(xs, ys):
    order, seen = [], set()
    for x, y in zip_longest(xs, ys):
        for i in (x, y):
            if i is not None and i not in seen:
                seen.add(i)
                order.append(i)
    return order


def _vector_fusion(order, vec):
    """keyword order + meaning order, vector weighted 2: gold chunk in top 3 for 9/14 questions vs 4/14 for keywords alone"""
    sc2: dict = {}
    for w, lst in ((1, order[:80]), (2, vec)):
        for r, i in enumerate(lst):
            sc2[i] = sc2.get(i, 0) + w / (60 + r)
    return [i for i, _ in sorted(sc2.items(), key=lambda x: -x[1])]


def _rank_boosted(con, sh, tokens, noisy):
    """(a) -> ({span id: (score, why)}, ids best first): symbol hits keep their score, keyword hits get 20 minus a rank penalty."""
    a: dict = {}
    for i, sc, why in sh:
        a[i] = max(a.get(i, (0, ""))[0], sc), why
    for r, i in enumerate(_bm25(con, tokens, 60)):
        a.setdefault(i, (20 - r * 0.3, "keyword"))
    return a, [i for i, _ in sorted(a.items(), key=lambda x: -x[1][0] * (0.6 if noisy(x[0]) else 1))]


def _rank_fused(sh, lists, noisy):
    """(b) reciprocal-rank fusion of the symbol-hit list and the separate keyword lists."""
    sc: dict = {}
    for lst in ([i for i, _, _ in sh], *lists):
        for r, i in enumerate(dict.fromkeys(lst)):
            sc[i] = sc.get(i, 0) + (1 / (60 + r)) * (0.6 if noisy(i) else 1)
    return [i for i, _ in sorted(sc.items(), key=lambda x: -x[1])]


def retrieve(root, symbols, keywords, question="", limit=40):
    """-> [{path, start, end, score, why}] best first. Two rankings interleaved (measured: the union finds the gold chunk where either alone often misses):
    (a) one merged OR query with symbol hits boosted, (b) reciprocal-rank fusion of separate lists: symbol names, model keywords, symbol words, question words."""
    con = connect(root)
    info = {}

    def span(i):
        if i not in info:
            info[i] = con.execute("select path, start, stop from spans where id=?", (i,)).fetchone()
        return info[i]

    def noisy(i):
        """Tests, docs, json, css rank below real source."""
        return any(n in span(i)[0] for n in NOISE)

    sw = [w for t in symbols for w in words(t)]
    kw = [w for t in keywords for w in words(t)]
    qw = words(question)
    sh = _symbol_hits(con, symbols)
    a, ranked_a = _rank_boosted(con, sh, list(dict.fromkeys(sw + kw + qw)), noisy)
    ranked_b = _rank_fused(sh, [_bm25(con, kw), _bm25(con, sw), _bm25(con, qw)], noisy)
    order = _interleave(ranked_a, ranked_b)
    vec = vector_ranked(con, question) if question else []
    if vec:
        order = _vector_fusion(order, vec)
    out = [
        {
            "path": span(i)[0],
            "start": span(i)[1],
            "end": span(i)[2],
            "score": a.get(i, (0, "keyword"))[0],
            "why": a.get(i, (0, "keyword"))[1] if i in a else "meaning",
        }
        for i in order[:limit]
    ]
    con.close()
    return out


def query(root, text, k=3):
    """-> up to k non-overlapping chunk dicts {path, file, start, end, text, score, why} for a plain-words TEXT, best first (keyword ranking only, no model).
    Builds the index when there is none; refresh() before a batch of queries to pick up edits."""
    root = os.path.abspath(root)
    if not os.path.exists(os.path.join(root, ".code-index", "index.db")):
        refresh(root, embed=0)
    out: list = []
    for c in retrieve(root, [], [text], limit=k * 6):
        try:
            with open(os.path.join(root, c["path"]), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:  # file removed or unreadable since indexing: no lines to show, skip the chunk
            continue
        end = min(c["end"], len(lines))
        if any(o["path"] == c["path"] and abs(o["start"] - c["start"]) < CHUNK for o in out):
            continue
        out.append(dict(c, file=c["path"], end=end, text="\n".join(lines[c["start"] - 1 : end])))
        if len(out) == k:
            break
    return out


def _model():
    """The model client, imported only when a call wants the model (--no-model never loads it)."""
    from localmodel import askmodel

    return askmodel


def _judge_batches(m, root, question, fresh, batch, terms, judged):
    """First batch of not-yet-judged candidates with a verified hit, else []."""
    for i in range(0, len(fresh), batch):
        part = fresh[i : i + batch]
        LAST["all"].extend(part)
        judged.update((c["path"], c["start"]) for c in part)
        keep = [part[j] for j in m.judge(root, question, part)]
        keep = [c for c in keep if any(t in m.snippet(root, c).lower() for t in terms)]  # cited code must mention a query term
        if keep:
            return keep
    return []


def _retry_terms(m, root, question, vocab, cands):
    """Round 2 search terms: the model re-asked with names seen in the round-1 candidates."""
    symbols, keywords = m.translate(question + " (the first attempt found nothing; try different names and synonyms)", vocab, seen_names(root, cands[:10]))
    return symbols, keywords + words(question)


def _ask_result(answer, idk, closest, tried, changed):
    return {"answer": answer, "idk": idk, "closest": closest, "queries": tried, "reindexed": changed}


def _ask_setup(root, question, model):
    """Refresh the index (no wait), reset LAST, and translate the question -> (reindexed flag, retry vocabulary, model, symbols, keywords)."""
    changed, _gone = refresh(
        root, embed=int(os.environ.get("ASK_CODE_EMBED_BUDGET", "400")), wait=False
    )  # a first call on a big repo must not block for minutes: finish with `local.py index`
    LAST["all"] = []
    vocab = (
        vocabulary(root) if model else None
    )  # only for the retry: on round 1 it pulled the model to generic repo words and hurt ranking (6/14 vs 9/14 gold in top 10)
    m = _model() if model else None
    symbols, keywords = m.translate(question) if m else ([], [])
    return changed, vocab, m, symbols, keywords + words(question)


def ask(root, question, model=True, rounds=2, batch=10, depth=40):
    """-> {"answer": [cands], "idk": bool, "closest": [cands], "queries": ...}
    Judges the ranked candidates in batches of `batch` down to `depth`, stopping at the first batch with a verified hit; round 2 re-asks the model with names seen in round 1."""
    changed, vocab, m, symbols, keywords = _ask_setup(root, question, model)
    tried: list = []
    judged: set = set()
    for rnd in range(rounds):
        cands = retrieve(root, symbols, keywords, question, depth)
        tried.append({"symbols": symbols, "keywords": keywords[:12]})
        if not model:
            return _ask_result(cands[:3], not cands, cands[3:6], tried, changed)
        terms = {w for s in symbols + keywords for w in words(s)}
        keep = _judge_batches(m, root, question, [c for c in cands if (c["path"], c["start"]) not in judged], batch, terms, judged)
        if keep:
            return _ask_result(keep[:3], False, [], tried, changed)
        if rnd + 1 < rounds and m:
            symbols, keywords = _retry_terms(m, root, question, vocab, cands)
    return _ask_result([], True, retrieve(root, symbols, keywords, question, 3), tried, changed)


def _build_index(root, a):
    """--index: build everything now, including all embeddings."""
    t = time.time()
    refresh(root, fresh="--reindex" in a)
    con = connect(root)
    chunks = con.execute("select count(*) from spans").fetchone()[0]
    embedded = con.execute("select count(*) from vecs").fetchone()[0]
    con.close()
    print(f"indexed {chunks} chunks, {embedded} embedded, in {time.time() - t:.0f}s")
    return 0


def _use_model(a):
    """True when the model is wanted and reachable (a notice is printed when it is not); --no-model never loads the client."""
    if "--no-model" in a:
        return False
    m = _model()
    m.set_think("--careful" in a)
    return m.use_model(a)


def _print_notices():
    if LAST.get("busy"):
        print("(the index is being built right now by another process: results may be incomplete, retry in a minute)", file=sys.stderr)
    if LAST.get("pending"):
        print(f"(semantic index still building: {LAST['pending']} chunks not embedded yet; `local.py index` finishes it)", file=sys.stderr)


def _print_result(r):
    if r["idk"]:
        print("IDK: nothing in the index answers this with confidence.")
        for c in r["closest"]:
            print(f"  closest (unverified): {c['path']}:{c['start']}-{c['end']}  {c['why']}")
        return 1
    for c in r["answer"]:
        print(f"{c['path']}:{c['start']}-{c['end']}  {c['why']}")
    return 0


def main(a):
    if not a or a[0] in ("-h", "--help"):
        sys.exit(__doc__)
    root = os.path.abspath(a[a.index("--root") + 1]) if "--root" in a else find_root()
    if "--index" in a:
        return _build_index(root, a)
    if "--reindex" in a:
        refresh(root, fresh=True, embed=0)
    q = next((x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or a[i - 1] != "--root")), "")
    r = ask(root, q, model=_use_model(a))
    _print_notices()
    if "--json" in a:
        print(json.dumps(r))
        return 0
    return _print_result(r)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
