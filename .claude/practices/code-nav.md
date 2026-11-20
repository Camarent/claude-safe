# Code navigation (read before searching a repo)

Use `lp ...` instead of grep-and-read when you need to locate code. Run it from the project root. In the container `lp` is a short alias that forwards every argument to it (`lp find "q"` = `local.py find "q"`).
root = repo (.git, dir or worktree file); fallback nearest .code-index; `--root` overrides.

## Which command

| Question | Command | Model? |
|---|---|---|
| Where is `X` defined? | `local.py nav def FILE X` (X as it appears in FILE) or `nav symbols X [--lang py\|js\|ts\|cpp\|rs]` | no |
| Who uses `X`? | `local.py nav refs FILE X` | no |
| Type or doc of `X` | `local.py nav hover FILE X` | no |
| What is in this file? | `local.py nav outline FILE` | no |
| Type or compile errors in a file (py, js/ts, rust, cpp; a missing server prints `NOT CHECKED`, exit 3) | `local.py nav diag FILE` | no |
| "Where do we handle refunds?" (no exact name) | `local.py find "question"` | yes (falls back to keyword search if the local model is down) |
| Does each file meet these criteria? | `local.py judge --criteria "text" FILE...` | yes |
| Is this diff right for this brief? | `local.py review --brief B --diff D [--tests T]` | yes |
| Long reads, narrow code tasks | `local.py bulk\|pack\|code\|log ...` (localdev.py) | yes |
| Build the whole search index now (first use on a big repo) | `local.py index [--root DIR]` | no (CPU embeddings) |
| Who holds the model slots? | `local.py status` | no |

Exact name known: use `nav`. Only a description: use `find`. `find` is a locator, not an oracle.

Usage: `judge` checks criteria over named files; `review` checks a diff against the brief; `nav hover` gives a signature without opening the file; `code` does one narrow code task against a test file. Simple counts: plain `grep -c`, not `bulk`. `pack` is advisory (a start, not a rule): verify its picks with grep/nav. `nav diag` covers py/rust only.

## Reading `find` output

- `path:START-END  why` lines are verified: the model quoted a line that exists in that range. Open only that range.
- `IDK` means nothing in the index answered with confidence. Do not guess and do not start scattering greps. Say IDK in your report, or retry with exact identifiers via `nav symbols`. `closest (unverified)` lines are hints only.
- A message "no local model reachable" means keyword search only: treat every hit as unverified.
- `find --careful` adds reasoning to the judge: about 33 s instead of 7 s, fewer false hits. Use it when a wrong citation would cost more than the wait.
- Measured (one repo, 14 casual questions, 2 runs): right file in the top 3 for 78%, right chunk for 71%, 7 s per query; 7 of 8 questions about things not in the repo correctly return IDK (8 of 8 with `--careful`). Questions that quote the code's own comments score 96-100%. Roughly 2-3 in 10 casual questions still miss: if `find` gives IDK or something odd, fall back to `nav symbols` with guessed names, then grep.

## Index

`find` keeps `.code-index/` in the project root (self-ignoring via its own `.gitignore`): ctags symbols, one chunk per function (with its leading comment), BM25 over the chunks, and a small embedding vector per chunk (fastembed + bge-small, CPU, baked into the image). It refreshes itself on every call: files whose mtime or size changed are re-parsed and re-embedded, deleted files are dropped, so a checkout or pull is picked up.

- Dot-folders are skipped (`.cache`, `.wt`, `.ruff_cache`, ...) except `.github`, `.notes` and `.githooks`.
- First use on a big repo: `lp index [--root DIR]` builds everything once (about 140 s per 5000 chunks, 500 files). Without it, each `find` embeds at most 400 new chunks and prints "semantic index still building"; results are valid but rank on keywords until the vectors exist.
- Full rebuild: `index --reindex`. A schema change in the tool rebuilds the index by itself once.
- No embedding library (an older image): `find` works on keywords and symbols only.

## Language support

- Python, JS and TS: always available (pyright, tsc --lsp (TypeScript 7)).
- C and C++: clangd, present with `--with cpp`. Needs `compile_commands.json` at or above the file (`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or `bear -- make`), else only single-file answers.
- Rust: rust-analyzer, present with `--with rust`. First call in a project can take 5-10 s while it loads.
- A language whose server is missing prints `server failed to start`; `nav symbols` and `find` still work (ctags), for every language.

## Warm server

`find` and `index` run in a small background server (`codesrv.py`) that starts itself on the first call, keeps the embedding model loaded, serves several agents at once and exits after 30 idle minutes. If it cannot start, `local.py` runs the same thing in-process (slower by about half a second, same answers). `LOCAL_NODAEMON=1` forces in-process. If a Strata model is expected but still loading, `find` waits for it (first call can take a minute or two); with no model at all it answers from keywords and says so. `nav def|refs|hover|outline|diag` runs in the same warm `codesrv.py`. Language servers stay up in a pool, one per (language, project root): LRU cap `LSP_POOL_MAX` (default 6), idle close after `LSP_POOL_IDLE` seconds (default 600). The pool is gone when `codesrv.py` exits. Edits are picked up without a restart: each open document is re-synced when its mtime and size change, and re-read by content hash when queried. Git worktrees are separate pool entries. A pool failure, timeout or missing server falls back to the per-call path (start the server, answer, stop). `nav symbols` and `LOCAL_NODAEMON=1` always use the per-call path.

## Limits worth knowing

- JS CommonJS: `const { f } = require('./x')` is a separate binding for tsserver. `nav refs` therefore also prints "more word-match hits (text search, not verified)". Treat those as candidates.
- Global-attached modules (`root.SomeModule = ...`) are invisible to the language server; use `nav symbols` and `find`.
- Many agents may call at once: every model call waits for a free slot in localq (1 at a time: one request fits the 128k window, overlapping calls ran ~2x slower). Do not run your own copies of the model.

## Hybrid search
Unknown location: `local.py find "question"` first. Known file/symbol or exhaustive callers/impact: grep or `nav`. Exclude minified vendor JS from grep (`--glob '!*.min.js'` / `grep --exclude='*.min.js'`). Find-first is guidance, not mandatory.
