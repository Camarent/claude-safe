# Working on the pipeline itself

Read this before changing anything under `.claude/tools/`, `agents/`, `commands/` or `practices/`. This file is about the claude-safe repo itself; projects use `practices/branches.md`.

## Layout
```
claude-safe/                      git repo (this repo only: master + dev); push only master, only on the owner's word
  pyproject.toml, uv.lock         dev group only (ruff, mypy, pytest, pytest-cov, coverage, vulture, bandit, lizard); runtime is stdlib
  scripts/dev.sh                  the one wrapper for checks (see below); everything lands in ./.uv
  AUDIT.md                        tools and thresholds for auditing this repo
  .claude/
    tools/                        entry scripts at their old paths (plan.py wave.py orch.py local.py qrun.py ...), shared helpers
      pipeline/                   core package; configuration/ holds HOME, TOOLS_DIR, AGENTS_DIR, PRACTICES_DIR, tool_cmd()
      localmodel/                 optional local-model (Strata) tools + eval/ + data/ (localdev policy and profiles)
      tests/                      unit and synthetic tests; tests/support builds throwaway projects; tests/integration needs a real project
      ptest.py                    THE entry point for the pipeline test suite: one file per process, in parallel (~20 s for all files on the host, ~22 s in the container); also the container entry point. Never use serial `unittest discover`
    agents/ commands/ practices/ skills/ githooks/ git/   as before
    parked/MANIFEST.md            the only place parked features live (rules inside)
```

## Rules
- Main-dirt guard: `maincheck.py` reports a dev's writes to the main checkout. The `main_guard_ignore_globs` pipeline.json key sets the path globs it never reports; default `("qrun-*.log",)` as `MAIN_GUARD_IGNORE_GLOBS` in `limits.py` under `.claude/tools/pipeline/configuration/`. It replaces the default when set. Never a reason to delete or revert the paths it names.
- No literal `/root/.claude` in code: use `pipeline.configuration` (`TOOLS_DIR`, `tool_cmd("x.py")`, ...). It derives the home from the file location; env `PIPELINE_HOME` overrides.
- No project names or `/workspace` in core code or shared docs. Per-project values live in the project's `pipeline.json` (`repo`, `test_env`, `each`, `gates`, branches) and its own CLAUDE.md / AUDIT.md.
- Values (branch names, paths, timeouts, tool lists, flags) come from `pipeline.configuration` as named settings with a `pipeline.json` or env override, never as literals scattered in code. `/workspace` appears in pipeline code only as one reference constant in configuration. A literal in code is a design smell.
- Local-model-only code goes in `tools/localmodel/`; core must work with Strata down.
- Parked = `parked/<name>/` + a MANIFEST entry. Dead experiments are deleted (git history and tags are the archive).
- Open work goes in `practices/pipeline-backlog.md`, nowhere else.
- Tests that start real processes or wait on clocks and sockets carry `@timing_sensitive` (`tests/support/timing.py`): off by default, run with `TIMING_TESTS=1`. They flake while a code-index build or the local model loads the host; the gate must not depend on them.
- Tests clean up after themselves (temp dirs, files, sockets, processes, worktrees, env): see `.claude/CLAUDE.md`. `ptest.py` and `tests/conftest.py` set a scratch `TMPDIR` per run as a safety net only.

## How to add a step / an agent
Step (a driver step in `pipeline/phases.py` or `audit_phases.py`):
1. Add its id as a named constant in `pipeline/configuration/` (no literal id in phases code).
2. Add the phase function (yields `step(...)` lists) and call it from the stage graph; respect the flags in `pipeline/graph.py`.
3. Add the verdict words it may answer to `ROLES` in `configuration/verdicts.py` if it reads a report.
4. Add a line to the step list in `practices/orch-pipeline.md` (stage-end order).
5. Test: a graph/phase test in `tests/` (exact step list) and a test for its verdict parsing; then `scripts/dev.sh check`, `ccn`, `test`.

Agent (a file in `agents/`):
1. Create `agents/<name>.md` with frontmatter `name:` (= file name), `description:` (one line), `model:` (`haiku`, `sonnet` or `opus`).
2. If it is an auditor or has a model table, add it to `configuration/` (`AUDITORS`, `AUDITOR_START_MODEL`, `ROLES`) and the turn cap if needed.
3. Add it to `practices/orch-pipeline.md` (model ladder) when it changes the model route.
4. Run `scripts/host_agents.py` (dry run) after merge to master; tests `test_agent_frontmatter.py` and `test_agent_model_tables.py` must pass.

## Daily commands (host or container, from the repo root)
| command | does |
|---|---|
| `scripts/dev.sh sync` | create the repo-local venv from `uv.lock` (cache, interpreter choice and venv stay in `./.uv`) |
| `scripts/dev.sh check` | ruff lint, ruff format check, mypy. Must be clean |
| `scripts/dev.sh fix` | ruff format + safe autofixes (F401 stays manual: some unused imports are re-exports) |
| `scripts/dev.sh test [PATTERN...]` | unit and synthetic tests via `ptest.py` (parallel); `dev.sh pytest [args]` = serial pytest for `-k`; container-only tests skip with "container only: X not installed" |
| `scripts/dev.sh itest` | opt-in: needs `PIPELINE_PROJECT_DIR=<project dir with pipeline.json>` (no default; exits 2 without it); the real project's settings are used read-only, the project is not modified |
| `scripts/dev.sh cov` | tests with branch coverage; fails below `fail_under` (90) |
| `scripts/dev.sh dead` / `sec` | vulture / bandit (reviewed skips and `# nosec Bxxx  # reason` markers) |
| `scripts/dev.sh clean` | delete `./.uv` and all caches: the host is clean again |
In the container the full suite also runs as `cs-ptest`. After a stage-sequence change read `cs-wave run --stage S --dir D --dry-run` and add a case to `tests/test_stage_sequence.py`.

## Commands and paths
- `.claude/paths.json` is the single place to remap on a move: roots (`/root/.claude`, `/workspace`, host dirs) and the `cs-*` command names.
- Container commands are `cs-<key>` (prefix `cs-`; `lp` keeps its name). Wrappers live in `.claude/bin/` and find the home from their own location. Regenerate after editing the index: `python3 -I /root/.claude/tools/cmdindex.py gen-bin`.
- Host agents get absolute paths: `scripts/host_agents.py` swaps the roots and `cs-*` names when it renders agent copies.
- Hooks in `settings.json` run the guards as `cs-cmd-guard`, `cs-review-guard`, `cs-model-guard` (`command -v X && exec X || exit 2`: a missing wrapper blocks instead of passing). The image needs `/root/.claude/bin` on PATH (Dockerfile).
- Host-only commands (`run`, `dev`, `release`, `image-build`) keep their `scripts/*.sh` form.

## Release and agents (claude-safe repo only)
- `scripts/release.sh` is the pre-PR gate on the current branch: ruff, mypy, tests with the coverage floor, vulture, bandit, the whole suite in the container image (`RELEASE_IMAGE`, default `claude-safe:rust`), gitleaks on files and history. It never merges and never pushes. GitHub CI (`.github/workflows/ci.yml`) runs the host checks and gitleaks again on every PR; it does not run the container suite.
- `scripts/host_agents.py` writes host-ready `pl-*` copies of the agents to `~/.claude/agents` (no `lp`, no container paths, only tools that exist on the host). Dry run by default; re-run with `--apply` after changing an agent or after merging to master (`--home` points the text at the live copy).
- A container run needs no setup: `docker run --rm --network none -v "$PWD":/src:ro -w /src --entrypoint python3 claude-safe:rust -I /src/.claude/tools/ptest.py`.

## Branch flow of the claude-safe repo
Same one flow as every repo (`practices/branches.md`): `master` is the only long-lived branch, work happens on a short branch, a PR carries it, CI must be green, squash-merge. The main checkout stays on `master`: the container mounts it as `/root/.claude`, so never switch it to a branch while a stage runs. Work in a separate worktree: `git worktree add ../claude-safe-<topic> -b <topic> origin/master`. One commit per meaning (`type(area): what`, `TZ=UTC`, `-F msgfile`). Before the PR: `scripts/release.sh` clean. Then `git push -u origin <topic>`, `gh pr create`, wait for the `ci` checks, `gh pr merge N --squash --delete-branch` (no confirmation needed once green; tags and releases need the owner's word). Afterwards `git pull --ff-only` in the main checkout, `git worktree remove` the topic worktree, `scripts/host_agents.py --apply` if an agent changed.

## Who edits the pipeline code (claude-safe sessions)
The session you talk to (the lead) keeps a thin context: it plans, decides, reviews and merges. It does not write pipeline code itself when a gate can judge the change.
- **Code change with a test** (fix, new tool, refactor): dispatch the `dev` agent (Agent tool, `subagent_type: dev`; Haiku first, Sonnet only after it ESCALATEs or fails, see `agents/dev.md`) with a brief. In a host session the same agent is called `pl-dev` (the copy `scripts/host_agents.py` writes to `~/.claude/agents`). One agent per change, in its own worktree (`isolation: worktree`), never two on the same files.
- **Lead does itself**: docs and backlog text, one-line config values, a merge, a decision. Reading code to decide is fine; use `lp nav` / `lp find` or the `scout` agent (`pl-scout` on the host), not grep.
- After the agent returns: `git diff --stat`, then `scripts/dev.sh check` and `scripts/dev.sh test`. Read the diff only where the stat looks wrong. Red: send the output back to the same agent, not a fix by hand. Commit on the topic branch (one commit per meaning), open the PR, squash-merge when CI is green.

Brief template (paste into the prompt, fill every line):
```
Task: <one sentence: the behaviour that must change, with the evidence (event, file:line, log line)>
Where: <files/functions the planner expects; a prediction, not a limit>
Rules: read .claude/practices/pipeline-dev.md; no literals in code (named setting in pipeline/configuration/); stdlib only at runtime
Test: add or change a test in .claude/tools/tests/; it must fail before and pass after; tests clean up temp files and processes
Done when: `scripts/dev.sh check` clean and `scripts/dev.sh test` green; reply with changed files, test names, and anything you did not do
Out of scope: <what not to touch>
```

## Public-words gate (`public_forbidden_words`)
- Setting: the `public_forbidden_words` list in the project `pipeline.json` (key `PUBLIC_FORBIDDEN_WORDS_KEY` in `pipeline/configuration/public.py`). Default: no words, so the gate finds nothing. The env var `PIPELINE_PUBLIC_FORBIDDEN_WORDS` (comma-separated) overrides the key when set. Private words go in that env var (set locally or in an untracked shell profile), never in `pipeline.json`: the file is tracked and public.
- Scan: every tracked text file (files with a NUL byte in the first `PUBLIC_BINARY_SNIFF_BYTES` are skipped), line by line, case-insensitive, plain text not regex. Blank words are ignored (an empty word would match every line). Code: `pipeline/public_words.py`.
- Docs skip: the gate returns clean without a scan when the words list is empty or the repo is not public. A private repo never fails on its docs.
- Public-words gate: `ops_steps.py public-words --dir D` prints `file:line: word` for every hit and exits non-zero on any hit. The dev brief tells the dev to run it before VERDICT: DONE and to fix every line it prints (`phases.py`, the docs step).
- Tests: `.claude/tools/tests/test_ops_steps.py` holds the public-words cases.

## CI reads (pr-ready)
- CI status comes from `gh run list --commit <head sha>` (`pipeline/pr_ops.py` `_ci_runs`), never from `gh pr checks`: a token without the checks scope cannot read the latter.
- `&&` lint: a CI line `a && b` counts as known when each part `a`, `b` is a gate command. The same split feeds `ci_drift` (`ci_drift.py`), so a chained lint line is not reported as drift.

## Rework path note
- A rework or retry note names the file, the line and the replacement text (task-12 rule; `cli_orch.py`, `ORCHESTRATOR.md`).

## Mechanical changes
Moves, renames and rewrites go through a script with a dry run first (print the plan, `--apply` to execute), then the tests decide.

## Lesson: agent cwd must be the tree it edits
- What happened: a dev session started with cwd = the main checkout, so its relative paths and Edit calls wrote into main; MAIN-DIRT then blamed the job and blocked every other job.
- Rule: a dev agent's cwd is its job worktree. The reviewer and read-only roles keep the project cwd. The lesson applies to any role that edits: give it the tree it edits as cwd, and reach the rest through `--add-dir`.
- Fix: dev cwd = worktree (`claude.py`); `write_guard` (`tools/write_guard.py`) denies main-checkout writes from a dev session; the guard log names the writer for MAIN-DIRT.

## Lesson: red base gates and nested course repos
- A job worktree holds a nested repo (`workspace/<course>`) as a link to the main copy unless the task lists its files. A link is not read-only: a dev that edits through it changes the shared repo, and the merge step fails later. Snapshot the link at dev start, compare after (`linkguard.py`), revert tracked changes with a patch kept, report the rest, and give the dev `wt.py add-repo` for the real need; never ask the lead.
- Parallel task gates share the linked repo: a test that writes a fixed file in its own folder collides with the same test in another task (ENOENT on `tools/test-*.jsonl`). The base check cannot clear it (the base run is alone and green). A task that changed nothing skips the gates, which removes most of these runs.
- A dev that sees a red gate on the base must not stop with BLOCKED or fix it; the brief and `agents/dev.md` say so. A rule that sends a base-red test to BLOCKED contradicts the driver's base clearing: check both when changing either.
- An event with a `task` key must carry what the retry ladder reads (`role` on a dispatch or report): a log-only event uses another key (`forced_remove` uses `job`).
- Messages and file names for this live in `pipeline.configuration` (`LINK_*`, `BASE_WHY_*`, `CLEAR_WHY_*`).
- Time-based or non-isolated tests are bad design (code not separated): inject the clock, isolate the test; only if impossible put it in `gates_merge`. See `agents/dev.md`, `agents/architect.md`, `ORCHESTRATOR.md`.
- Main-checkout guard: `main_guard_ignore_globs` (pipeline.json key; default `("qrun-*.log",)` from `MAIN_GUARD_IGNORE_GLOBS` in `limits.py` under `.claude/tools/pipeline/configuration/`) lists path globs of the main checkout the dirt check never reports. Set it in the project `pipeline.json`; a list replaces the default (`[]` ignores nothing).

## Lesson: a missing hook binary on Edit or Write must fail open
- What happened: a fail-closed write hook blocked every dev write in job worktrees (2026-11-24) because its binary was not on PATH.
- Rule: a write guard on Edit or Write must exit 0 when its binary is missing (fail open). Hooks on Bash and Agent keep exit 2 on purpose (fail closed). The review guard, matched on Edit or Write too, stays fail closed by decision.
- Proof: `test_hook_commands.py` runs each fail-open hook with an empty PATH and asserts exit 0; `unguarded_names` flags any cs- hook without a guard form.

## Lesson: UI jobs need a grounded process
- What happened: screens were drawn without a glossary and inventory; the result had dead links and invented terms.
- Rule: a UI job starts from a glossary (the owner-approved terms), journeys (the paths a user takes), and an inventory (every screen and link). The owner signs off the glossary, then again before the prototype is built.
- Proof: `ui_inventory_check.py` checks the inventory against the journeys and flags dead links and terms not in the glossary.
