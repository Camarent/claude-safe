---
name: prepare-repo
description: Make an existing git repo ready for the pipeline (plan.py / wave.py) before any change. Use when the user says "prepare this repo for the pipeline", "bootstrap pipeline", or before the first plan.py run in a repo that has no pipeline.json. Creates the local, git-excluded pipeline files and checks the first-run traps.
---

# prepare-repo

Do this BEFORE editing the repo by hand and before the first `plan.py run`. Hand edits first make the tree dirty and `plan.py` stops with `blocked: uncommitted changes`. "It is only mechanical work" is not a reason to skip the pipeline.

Pipeline files stay local: their entries go to both `<repo>/.git/info/exclude` and the tracked `<repo>/.gitignore` (the tracked file gets the entries appended at the end, a blank line before the block if needed; created if absent). The lead commits the `.gitignore` change before the first `plan.py run`, since a dirty tree blocks it.
`pipeline.json`, `AUDIT.md`, `.pipeline/`, `.work/`, `.wt/`, `.audits/`, `.code-index/`, `.notes/` (retro incidents land there)

Run `cs-prepare-repo --dir <repo>` to write these entries (idempotent, append-only, an existing line is never changed; `--dry-run` only prints the `would add:` list, a real run prints `will add:` first; the file's own line endings are kept; nested repos also get the globs of `pipeline.json` `nested_repo_excludes` in their exclude file and `.gitignore`, none by default). It also appends the `.gitignore` lines of the languages it detects by marker files (`__pycache__/` for Python, `node_modules/` for Node, `target/` for Rust; a folder the repo already tracks or negates is skipped), writes a git-excluded `CLAUDE.md` with a `## Docs` section when the repo has a docs folder and no `CLAUDE.md` (an existing one is never edited, only reported), and with `--add-worktrees` inserts `"worktrees": true` into `pipeline.json`. After the list it prints `report:` lines for things it does not write: malformed AUDIT.md audit-tools lines, a missing `worktrees` setting, an existing CLAUDE.md without `## Docs`, gate tools missing from PATH, a gate python without pytest. Project-specific rules and lessons go into a note the project's CLAUDE.md indexes with a one-line "read when ..." pointer; create it only when there is a real lesson, never an empty file, and never name project things in shared pipeline files.

## Steps
1. **Pipeline root.** If the repo sits inside a non-git workspace next to other repos (`wt.nested_repos` scans them, their dirt blocks the run), make the repo its own root: files go in `<repo>/`, every call passes `--dir <repo>`. The `/run-stage` wrapper runs from the project root, so for such a repo call `wave.py run --stage S --dir <repo>` directly.
2. **Clean tree.** Commit own earlier edits first, and the `.gitignore` that prepare-repo wrote (`TZ=UTC git commit`, subject `<type>(<area>): <what>`). Commits never get a Co-Authored-By trailer in repos where the owner said so.
3. **Branch name.** The pipeline works on `stage/<feature>` (`branch_work`). If a hand-made branch exists, `git branch -m` it to that name, or its commits are lost to the new branch cut from master.
4. **pipeline.json.** `gates` = list of `{name, cmd}` (the project's lint/type/test commands; without gates the final test is a model run). Leave `max_tasks` unset (default 30) unless there is a reason. Validate with `python3 -c 'import json;json.load(open("pipeline.json"))'` after every hand edit (a trailing comma silently drops all settings).
5. **AUDIT.md.** A fenced ```` ```audit-tools ```` block, lines `name | cwd | command | parser`, parsers from `tools/audit/audit_parsers.py` (ruff, bandit, vulture, lizard, gitleaks, pip-audit for Python). Check with `cs-audit <repo>`.
6. **Release (only if the repo releases).** `release_script` in pipeline.json, a repo-relative bash script: `current [REF]` prints X.Y.Z read from the git ref (`git show REF:file`), `set OLD NEW ENTRY` edits the version files and prints their paths. Without it no version-bump or release step runs. The pipeline always bumps from the current version (patch default). First release of a repo with no tag: put `0.0.0` in the version files on the stage branch and write `release: minor` in TASKS.md, or the first tag is X.Y.(Z+1). Check `git tag` and `gh release list` first.
7. **Prompt and plan.** Write the goal to a prompt file (tasks, constraints, and a "not dev tasks" section for lead steps: squash, force-push, issue deletion), then `plan.py run --feature F --dir <repo> --prompt-file P --dry-run`, then for real, then `plan.py check`.
8. **Run.** `wave.py run --stage F --dir <repo>` with `run_in_background`, plus a `ScheduleWakeup` fallback of 1200 s or more.

## Known traps
- prepare-repo detects languages by root marker files (`pipeline/guidelines.py`) and writes a `## Guidelines` section (one `<lang>: read <dir>/<lang>.md ...` line each, plus `all: .../base.md`, plus links to `practices/driver-runbook.md` and `practices/lead-rules.md`) into a new CLAUDE.md, like `## Docs`; an existing CLAUDE.md without it is only reported. Nothing is loaded by default.
- A dev agent cannot run `chmod` (classifier): the lead sets executable bits on hooks and scripts and commits them.
- `git commit` by the dev needs the hooks it just wrote not to block it; enable `core.hooksPath` only after the stage.
- Public-repo history rewrite (squash, force-push, tag move) is a lead step after the PR lands; it needs the owner's word and a backup bundle outside the repo first.
- The pipeline's `release` step creates the GitHub release itself. A tag-triggered release workflow must create the release only when none exists, otherwise attach with `gh release upload --clobber`, or the run fails with "release already exists".
- The `0.0.0` + `release: minor` first-release trick was undone by an audit fix task (it aligned the version with the CHANGELOG `0.1.0` entry), so v0.2.0 shipped. Put a line in the prompt/TASKS.md that `0.0.0` is intentional and the audits must not touch the version, or keep the CHANGELOG entry as `[Unreleased]` until the bump.
