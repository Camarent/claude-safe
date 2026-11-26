# Lead rules for the pipeline

Read this when you change the pipeline from a project session, plan a fix for a pipeline problem, or sync installed pipeline config.

1. Pipeline changes only in the source repo, on a `stage/<feature>` branch from `master`, merged by PR. Reapply to the installed config with the repo sync script. Never edit the installed copy by hand: edits are lost and unreviewed. Use a worktree when the checkout sits on another branch.
2. A permanent fix for any pipeline problem seen in any project goes into the claude-safe plan as a task or phase (`cs-plan phase --after P` or `cs-plan task`), not only into a lessons note or a lead workaround. Check the live `Plan.md` first.
3. claude-safe drivers run through the auto-mode wrapper by default (permission mode auto), because their tasks edit files under `.claude/` that acceptEdits blocks. Never use the wrapper for other projects without asking. The addendum documents the wrapper.
4. Sync from a working stage branch is allowed when a phase just finished green and no driver is mid-step. Use a detached worktree of the branch, run the provision script with `--only claude-config`, run `rehost.py`, then remove the worktree. Never run `host_agents.py --apply`. Tell the owner which branch was synced.
5. Keep pipeline changes portable to the Docker image: no VM-only paths or assumptions in code or agent prompts. Do not list rebuild steps afterwards. Never run or list the Docker image test suite as pending.
6. Stale docs and tests are fixed in the same pass. After each stage, compare docs, decisions, module notes and tests with what the run measured. Add a new decision entry instead of rewriting old text. Gates for this are planned as a follow-up.
