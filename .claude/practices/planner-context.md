# Planner and audit context (plan_context.py, audit_context.py)

Read before touching `tools/localmodel/plan_context.py`, `tools/localmodel/context_eval.py`, the `context.*` settings or `tools/audit/audit_context.py`. Open items: `practices/pipeline-backlog.md`.

## Goal context (inside `cs-plan run`, before the architect)
1. `ensure_index` refreshes `lp index` for the repos in `<workspace>/pipeline.json` `context.index`. Nested repos are gitignored and have their own index.
2. `plan_context.goal_context`: `pick_root` chooses the nested repo named by a path in the request; `lp find` and `nav` leads; Strata (local model) names existing files per request line (lines of 6+ words); code ranks them by votes, groups them (code, tests, docs, data) and writes a digest. Result: `.work/<feature>/plan-context.md`.
3. The architect gets the request plus a pointer to that file. Model choice and ESCALATE: `practices/orch-pipeline.md` "Run".
Settings: `context` object in `pipeline.json` (`ignore` globs, `per_section`, `model`, `index`, `mode`). Strata down or failing: the step is skipped with one line in the file and flagged by the `context-*` retro signals.
- Mode `explore` (default; `batch` is the fallback): `localmodel/explore.py` checks each picked repo out as a detached git worktree; `agent_files` explores it with find/outline/def/refs/hover/symbols/diag/read/git plus a read-only shell (`safeshell.py`: allowlist head tail grep egrep sed ls cat wc sort uniq find echo cut jq, git ls-files|log|grep|show|blame, lp find|nav; no redirects, substitution, absolute or parent paths). The model answers by category (code/tests/docs/data, 15 each); `with_docs` adds docs that cite the chosen files.

## Audit context
`tools/audit/audit_context.py` writes `.work/<stage>/audits/audit-context.md` (the audit step passes `--workspace <project dir>`):
1. `--workspace W` first refreshes the index with `plan_context.ensure_index` (failures swallowed, Strata not needed).
2. Mechanical part, no model: outline and `lp nav refs` per changed file, capped at 6000 chars.
3. "Related, unchanged": `localmodel/related_files.py` `related_unchanged` asks Strata once per top-level folder of the changed files (max 4 groups; answers cached in `ctx-cache.json`), keeps names that exist, are not in the diff and carry one mechanical fact (a changed symbol or module name appears in the file). At most 10 lines / 1500 chars.
4. Strata down or any error: one line `(related files: skipped, local model down)`. `--no-model` leaves the section out. A `context-audit` event goes to the stage's `metrics.jsonl`.

## Phase context
`tools/localmodel/plan_context.py phase <repo> --feature F --phase NAME [--workspace W] [--out FILE]` writes `.work/F/phases/<dir>/plan-context.md` for the architect of phase 2..N: phase goal, paths, done_when; plan-notes decisions (whole file if <= 3000 chars, else lines naming the phase or its paths); `git diff --stat` release..work branch plus TASKS.md titles of earlier phases; file digest. `cli_feature.phase_ctx` runs it before the architect session (best effort, skipped with `PLAN_NO_CONTEXT`) and logs a `context` event with `phase` set. `--dry-run` builds nothing.

## Files
- `tools/localmodel/plan_context.py` (context, `ensure_index`, `live_recall`, CLI `goal|stage|index|phase`), `tools/pipeline/plan_run.py` (`code_context`, the `context` stats event), `tools/pipeline/metrics.py`.
- `tools/localmodel/context_eval.py` + `tools/localmodel/eval/*.{gold.json,prompt.md}`: `cs-context-eval <gold.json> --runs 3 --check-cache` (cold runs bypass the cache; the spread over runs is the noise floor). Run it after any change to the naming prompt, the Strata model or `context.ignore`.
- Tests: `test_plan_context.py`, `test_context_eval.py`, `test_retro_numbers.py` (`ContextSignalsTest`).
- Vector cache: `<workspace>/.code-index/vectors.db` (workspace = nearest folder with `pipeline.json`; `CODE_INDEX_CACHE` overrides; falls back to `~/.code-cache`). It lives in the project mount so a container restart does not force a full re-embed; all worktrees and nested repos share it. The `index-refresh` step (`index_step.py`) also vacuums `index.db` files that are over 50 MB and 25% free pages.
- Cache: `<repo>/.code-index/ctx-cache.json`; key = model id + prompt + per_section + file-list hash + request line.

## Gotchas
- A local model may hold most of the GPU (depends on the container profile): ASR must run on CPU. An unindexed repo makes `lp find` return "(no answer)" after its 90 s timeout; the first `lp index` of a large repo takes minutes.
- An index of the outer repo skips gitignored nested repos; `pick_root` fixes that when the request names a path.
- Experiments: cold runs for numbers, at least 3 samples, compare the spread before claiming a gain.
