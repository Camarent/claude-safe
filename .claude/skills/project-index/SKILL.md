---
name: project-index
description: Build or refresh the code search index of the current project (symbols, function-level chunks, embeddings) so `lp find` and the agents' code lookups are fast and accurate. Use when the user types /project-index, asks to "index the project", or before a session of heavy code exploration in a repo that has no .code-index/ yet. CPU only, no model or tokens needed.
---

# project-index

Builds `.code-index/` in the project root (it ignores itself in git). `find` also refreshes the index on its own on every call, but it embeds at most 400 new chunks per call; this command does it all at once.

## Steps

1. Run from the project root (or pass `--root DIR`):
   ```bash
   lp index
   ```
   Add `--reindex` only if the user asked for a rebuild from scratch or the index looks wrong.
2. It prints one line: `indexed N chunks, M embedded, in Ts`. Expect about 140 s per 5000 chunks the first time (500 files), seconds afterwards because only changed files are re-parsed and re-embedded. Run it in the background if the project is large and tell the user.
3. Report that line. If `M` is smaller than `N`, the embedding library is missing in this image: search still works on keywords and symbols, say so.
4. Mention once, if relevant: `.code-index/` is local; nothing needs committing.

Do not run `find` or other searches as part of this skill. Do not edit project files.
