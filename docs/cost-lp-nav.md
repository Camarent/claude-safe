Document body for `docs/cost-lp-nav.md` (I have no Write tool; lead saves this reply). Partial answers in a275 are kept. C1 wave-0 attribution and C2 lp-share cause are "not derivable" from the tree, as the lead accepted.

# Wave 0 cost and lp nav/find share

## C1. Where wave 0 spends its time

- Code-derived cause: job worktree setup. `.claude/tools/pipeline/worktree.py` `_run_setup` (:458) runs `worktree_setup` commands (:461) and `worktree_requirements` pip installs (:462, via `_requirement_steps` :447) in every NEW job worktree. Timeout :463.
- Each setup command logs `EV_SETUP` (`worktree.py:476`; defined `.claude/tools/pipeline/configuration/limits.py:291`) with keys `stage`, `for`, `cmd`, `rc`, `seconds`. No wave key.
- Defaults: `.claude/tools/pipeline/configuration/defaults.py:107-110` (`worktree_setup: []`, `worktree_requirements: []`). The cost exists only where a project sets them.
- Fix per cause:
  - Venv per job worktree: set `worktree_setup` / `worktree_requirements` in `pipeline.json` to reuse one venv.
  - Wave not attributable: add a `wave` key to the `EV_SETUP` event at `worktree.py:476`.
- Not derivable: that wave 0 is the dominant cost; the share of wave 0 in total cost; wave 0 identity. `.claude/tools/retro/agent_cost.py` has no wave concept (sections `_totals_section` :159, `_per_tool_section` :171, `_repeats_section` :183, `_unlimited_section` :196, `_growth_section` :206, `metrics_report` :308, `report` :329). No per-wave timing data in the tree.

## C2. lp nav/find share per agent prompt

- Code-nav sentence present at `.claude/agents/dev.md:11`, `.claude/agents/architect.md:11`, `.claude/agents/reviewer.md:11`. Same wording in `.claude/agents/scout.md:11` and `.claude/agents/arch-auditor.md:14`.
- Reviewer's "before grep" clause sits at `.claude/agents/reviewer.md:120`; architect's at :11 itself.
- Retro lp counter: none. `.claude/tools/retro/agent_cost.py` `LIMITER_RE` (:27) counts only `head|tail|grep`; `_count_tool_uses` (:78) counts per-command Bash/Read only. `lp` string in that file: 0 matches.
- Explore-ratio signal in code: 0 matches in `.claude/`. The only "explore" text is `.claude/ORCHESTRATOR.md:81` (context-pack rule, not a metric).
- Why lp is under-used: not derivable from code.
- Not found: the "0-7% of search calls" figure has no source in the tree.
- Proposed prompt change (unverified): move the lp sentence above the grep rules in `.claude/agents/dev.md`, `architect.md`, `reviewer.md`; state it as the first search step, not a "before grep" clause.
- To measure lp share, retro would need an lp counter in `_count_tool_uses` (`.claude/tools/retro/agent_cost.py:78`); not present.

## C3. Citations

Every path above is a repo-relative path that exists; function names are given beside each cite. Line numbers checked against the tree on 2026-11-28.

Test: `cs-qrun -- grep -q 'wave 0' docs/cost-lp-nav.md` passes only if the saved file contains the literal `wave 0` (it does in the heading-level text above).

Files changed: none (read-only; document is this reply).
Test result: not run (no tool write; grep check applies to the saved file).
ESCALATE: a275 said BLOCKED; I marked DONE because the lead accepted "not derivable" for C1/C2. Lead to confirm.
