# Global rules (all projects)

- Keep tool output short: Bash through `head`/`tail`/`grep`, large files via Read with offset/limit, never `cat`/`sed` dumps; `git diff --stat` first. Context stays thin.
- Code exploration (where is X, how does it work, who calls it) goes to the read-only `scout` agent; read yourself only what you will edit, and only the needed slice.
- Locating code: `lp nav def|refs|symbols|outline` (exact) or `lp find "question"` (fuzzy, cited or IDK) before grep; see `/root/.claude/practices/code-nav.md`.
- Pipeline work (agents, TEAM.md, scripts, checklists): when a run teaches a lesson, update the pipeline in the same pass, in every project.
- Before starting any job, planner run or pipeline stage: list the full scope and any unclear point (what belongs where, rules, open decisions) and get the owner's confirmation first. Ask with the AskUserQuestion tool (short options, one decision per question), never as a text list the owner must answer by typing. Never start, restart or replan on a guess; later changes go in as addenda.
- Pipeline mode (PLAN stages, running stages, dispatching agents): read `/root/.claude/ORCHESTRATOR.md` first. Not otherwise; agents never read it.
- Branches (all projects): only `master` is long-lived; one short-lived `stage/<feature>` branch per stage, squash-merged into `master` (pushed repos: through a PR, squash-merge without asking once CI is green; tags and releases need the owner's word). Details: `practices/branches.md`.
- If the classifier blocks a long or compound command (loop, `&&`, `xargs`, mass delete), do not work around it: split into short commands and run them one by one.
- New feature / big job: `cs-plan run --feature F --prompt-file P`, then `/run-feature F` (phase driver; a plan with `Plan.md`); never hand-dispatch the architect. See ORCHESTRATOR.md.
- Running a planned stage: `/run-feature <name>` (wraps `cs-wave run <name>`, from /workspace; old aliases `cs-wave feature` and `cs-wave run --stage` kept one release). Never launch stage agents by hand; see ORCHESTRATOR.md.
- No literals in pipeline code: branch names, paths, timeouts, tool lists and flags come from `pipeline.configuration` (named setting, `pipeline.json` or env override). `/workspace` appears in pipeline code only as one reference constant there. A literal in code is a design smell; see `practices/pipeline-dev.md`.
- Tests always clean up after themselves: every temp dir, file, socket, process, worktree or env change a test creates is removed when it ends (`addCleanup`, `tempfile.TemporaryDirectory`, `unittest.mock.patch.dict`). A tmpfs `/tmp` ran out of inodes after a few suite runs of leaking tests and stopped every command. `ptest.py` and `tests/conftest.py` give each run a scratch `TMPDIR` as a safety net; do not rely on it. After adding tests, check `ls /tmp | wc -l` does not grow across a run.
- Terse voice (caveman) for your own replies, always on; "stop caveman" or "normal mode" switches it off. Code, paths, commands, numbers and file contents stay exact.

@skills/caveman/SKILL.md
