# Local dev (Strata) - tools, profiles, limits

The local model does token-heavy work for free; Claude reads a 1-3 line result. Start it with `claude-safe --here --with strata`
(Strata runs beside your normal Claude session; model and memory needs depend on the container profile, see `profiles/`). All tools run in that container.

**The local model can be down, and then its steps are skipped.** `cs-plan run` and `cs-wave run` call `search.strata.preflight()` once before planning. If Strata does not answer, it sets `STRATA=0` for the run: `lp find` uses keyword/symbol search, planner/audit context steps skip with one line, and `localdev.py code|pack|bulk` print `skipped` and exit 0. Nothing is handed to cloud models instead. Set `STRATA=0` yourself to force this (`STRATA=1` or unset = probe the server). A run with `STRATA=0` logs `local_model: false` on its `context` and `context-recall` events: the explorer is not called, and the retro raises no context signals for it (no context is expected, so it is not a fault).

## Tools (`cs-localdev ...`, `cs-context-eval ...`; scripts live in `.claude/tools/localmodel/`, with its `.claude/tools/localmodel/data/policy.json` and `data/profiles/`)
| tool | what it does | status |
|---|---|---|
| `ask_code.query` | keyword search over the `.code-index` (shared with `lp find`); the pack builder uses it. | tested in `test_ask_code_query.py` |
| `localdev.py pack` | context pack for a planner/auditor: model proposes queries and judges hits, code copies excerpts verbatim (every cite valid) | rebuilt on search, unit-tested; first live run pending |
| `localdev.py bulk` | extraction/summary of files into ONE output file | live-tested OK (3 docs, 6 turns, exact quotes) |
| `localdev.py code` | a narrow task in its own git branch `localdev/<id>` (worktree under `.work/localdev/<id>/wt`); model edits the predicted paths, runs only the test command, may `git add/commit` there; the tool reruns the test, restores anything out of scope (even committed), commits leftovers on PASS | live-tested PASS (t4 fix, 21 turns, 82 s) |
| `localdev.py log` | recent local runs (mode, verdict, turns, secs, profiles) from the local run log (git-ignored, kept in the localdev work folder) | built |

## Writing a local brief (short on purpose)
A task file in the usual pipeline format is enough; these header lines are read: `Predicted paths:` , `Test (loop): \`...\``, `turns: N`, and optional
`Profiles: rust,js` (default: by file extension), `Read also: dir1, dir2` (read-only, max 3), `Run also: cmd1; cmd2` (extra commands).
The system prompt is assembled for you: `tools/localmodel/data/profiles/base.md` + profiles (rust, js, python, docs, pack, bulk) + the project's short
architecture (`.localdev/architecture.md`, else the first 400 words of `docs/ARCHITECTURE.md`). Add rules by editing/adding a profile file, not by growing briefs.

## What a brief may allow (ceiling in `.claude/tools/localmodel/data/policy.json`, owner-controlled)
Commands must start with an approved prefix (unittest, cargo test/clippy/build/check/fmt, wasm-pack build, node, npm test/run, scripts/, make, jq, rg, grep, git
status/diff/log) and contain no forbidden word (rm, curl, wget, sudo, git push/reset/rebase/clean, installs, `| sh`). `qrun.py --` wrappers are checked on the INNER
command. The model gets Read/Grep/Glob everywhere, Edit/Write only on the predicted paths, Bash only for the test command + approved extras + git add/commit.

## Where to use it (measured or reasoned; see the summary in PIPELINE.md)
1. Context packs for planners and auditors (replaces 20+ exploratory tool calls; the biggest measured planner cost).
2. Narrow code tasks with an exact test: renames, pattern copies, small bug fixes with a failing test, data/docs edits (local 18/18 on the bench, 21 turns / 82 s live).
3. Bulk extraction and summaries (the largest historical Claude spend was bulk agents).
Not for: planning/architecture, contracts or data formats, security, visual/UI judgement, tasks with no test.

## Known limits
One GPU: local jobs run one at a time. ~3x slower than Haiku per task (fine in the background). Files made in the container are root-owned on the host
(delete via a container). `git worktree` metadata lives in the repo's .git; remove finished ones with `git worktree remove --force <wt>` and `git branch -D localdev/<id>`.
