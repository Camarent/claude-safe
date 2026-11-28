# Wave 0 cost and lp nav/find share

Research for stage retro-m25-followup2, task-14. Two questions: (C1) what wave 0 spends, (C2) why `lp nav` / `lp find` is only a small share of search calls. Every path is repo-relative. Line numbers were checked against the tree on 2026-11-28.

## C1. Where wave 0 spends its time

**Code-derived cause: job worktree setup.**

- `.claude/tools/pipeline/worktree.py` `_run_setup` (line 458) runs the `worktree_setup` commands in every NEW job worktree. `_requirement_steps` (line 446) turns `worktree_requirements` globs into pip install steps.
- Each setup command logs an `EV_SETUP` event (`worktree.py:476`; defined at `EV_SETUP` in `.claude/tools/pipeline/configuration/limits.py`) with keys `stage`, `for`, `cmd`, `rc`, `seconds`. The event has no wave key. The gate kind constants `GATE_KIND_TASK`, `GATE_KIND_MERGE` and `GATE_KIND_FULL` (same `limits.py`; the last is the final full gate) name the `kind` of a `gate_cmd` record.
- Defaults: `.claude/tools/pipeline/configuration/defaults.py:107-110` set `worktree_setup: []` and `worktree_requirements: []`. The cost exists only in projects that set them. The `launcher` key (same `PROJECT_DEFAULTS`, default `""`, set in `pipeline.json`) costs nothing: it only makes a bare `cs-wave run` refuse unless the auto permission flags are set.

**Fix per cause:**

- Venv per job worktree: set `worktree_setup` and `worktree_requirements` in `pipeline.json` so one venv is reused.
- Wave not attributable: add a `wave` key to the `EV_SETUP` event at `worktree.py:476`.

**Not derivable from the tree:**

- That wave 0 is the dominant cost.
- The share of wave 0 in total cost, and wave 0 identity.
- `.claude/tools/retro/agent_cost.py` has no wave concept. Its sections are `_totals_section` (:159), `_per_tool_section` (:171), `_repeats_section` (:183), `_unlimited_section` (:196), `_growth_section` (:206), `model_costs` (:236), `metrics_report` (:308) and `report` (:329). The tree holds no per-wave timing data.

## C2. lp nav/find share per agent prompt

**Prompt wording (present, verified):**

- Code-nav sentence at `.claude/agents/dev.md:11`, `.claude/agents/architect.md:11`, `.claude/agents/reviewer.md:11`.
- Same wording at `.claude/agents/scout.md:11` and `.claude/agents/arch-auditor.md:14`.
- The "before grep" clause sits at `.claude/agents/reviewer.md:120` and `.claude/agents/dev.md:114`. In `architect.md` it is in the line 11 sentence itself.

**Retro counter (absent):**

- `.claude/tools/retro/agent_cost.py` `LIMITER_RE` (line 27) counts only `head|tail|grep`.
- `_count_tool_uses` (line 78) counts per-command Bash and Read calls only.
- The string `lp` does not appear in `agent_cost.py`. There is no lp counter, so the lp share cannot be measured from the tree.

**Other findings:**

- Explore-ratio signal in code: 0 matches in `.claude/`.
- The only related text is `.claude/ORCHESTRATOR.md:71`, a context rule ("Locating code ... uses `lp nav|find`"). It is not a metric.
- The "0-7% of search calls" figure has no source in the tree.

**Why lp is under-used:** not derivable from the tree. No code path records lp calls, and no transcript data is kept with the repo.

**Proposed prompt change (unverified):** move the lp sentence above the grep rules in `.claude/agents/dev.md`, `architect.md` and `reviewer.md`. State lp as the first search step, not as a "before grep" clause. Measure the effect only after an lp counter exists (add one in `_count_tool_uses`, `.claude/tools/retro/agent_cost.py`).

## Summary

| Question | Answer | Status |
| --- | --- | --- |
| C1 wave 0 cost cause | worktree setup (`_run_setup`, `EV_SETUP`) | code-derived |
| C1 wave 0 share | none | not derivable |
| C2 lp share cause | none | not derivable |
| C2 lp counter | missing in `agent_cost.py` | verified absent |
| C2 "0-7%" figure | no source | not found |
