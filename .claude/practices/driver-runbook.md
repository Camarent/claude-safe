# Driver watch runbook (generic, for every project)

Read this when you watch a running pipeline driver, when it blocks, or when you wake up to check a stage. A project keeps only its own specifics in its notes folder and links here.

## Wakeups
- Run the driver in the background from the project root. A driver that exits or blocks wakes the lead by itself.
- While no driver is blocked, schedule the fallback wakeup at the longest allowed delay and do not poll between. The wakeup prompt is a few lines: the task ids and a pointer to this file plus the project's own runbook. Never copy this text into the prompt.

## Check
- Read the tail of each driver output file (`tail -2`).
- Exit 3 means usage limit, exit 4 means BLOCKED. Read the block: `awk '/^BLOCKED, back/{f=1} f' <output>`; if empty, `tail -12`.
- Limit message "usage limit hit ... waiting N min": if the reset time has already passed, stop the driver and relaunch it.

## Fix a block
`python3 -I <config>/tools/orch.py retry|skip NAME --step X --note "file:line + replacement text"`, then relaunch the same driver in the background. NAME is the stage: a phase stage is named `<feature>/phases/NN-name`.
- Start and resume a planned feature with `cs-wave run NAME`, where NAME is the feature (a plan with `Plan.md`) or the single planned stage (with `TASKS.md`). Never start the wrong one. The older forms `cs-wave feature` and `cs-wave run --stage` still work as aliases for one release.
- A driver that stops with "blocked: uncommitted changes": inspect the files. Planner-written inputs (checker scripts, lists) that tasks name are committed to the stage branch; anything else is dropped. Use `--allow-dirty` only when sure.

## Known causes
- Decision or input files written into the git-ignored stage folder from a job worktree are lost when the worktree is deleted. The lead copies or rebuilds them.
- The generated `docs/INDEX.md` diff written by `wt.py commit` is accepted; the retry note says so.
- The cite check fails a report whose line number was valid before a split: edit the report to drop the number.
- A dev BLOCKED for a file only the lead can write means the lead writes it, then retries.
- The installed pipeline is the old copy until the sync script runs; fixes from a pipeline stage help only after the sync.

## After a pipeline-repo run
When CI cannot run, skip `pr-ready` and `master-merged` with a note after local gates are green, merge the PR with admin rights, then run the sync script and tell the owner to restart Claude Code. Anything outward (history squash, tag, visibility) needs the owner's word and a backup bundle outside the repo first.
