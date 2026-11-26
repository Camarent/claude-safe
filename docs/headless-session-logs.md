Research answer for task-1 (no file edited; the lead saves this reply as `docs/headless-session-logs.md` content).

**Where headless logs go (from tree):**
- Config dir: `~/.claude/projects` is hard-coded in `.claude/tools/retro/ledger.py:145` and `.claude/tools/pipeline/claude.py:311,336`. Only `.claude/tools/retro/denials.py:26-30` honors `CLAUDE_CONFIG_DIR`. The tree sets no `CLAUDE_CONFIG_DIR` for headless sessions (only `.claude/tools/localmodel/localdev.py:190`).
- Project folder name: the cwd with `/` and `.` replaced by `-` (`.claude/tools/pipeline/lead_context.py:13-15`). `ledger.find_transcript` globs `projects/*/`, so the folder name does not matter for lookup (`ledger.py:141-146`).
- File name: `<projects>/<folder>/<session_id>.jsonl` (`ledger.py:145`, `claude.py:311`). Subagent logs sit under `<sid>/subagents/`.

**The three candidate causes:**
1. Another config dir: not supported by the tree. Ledger and pipeline use fixed `~/.claude/projects`; no code sets a different config dir for headless runs.
2. Another project folder name for job worktrees: does not explain a miss. The glob is `projects/*/`, so any folder name matches.
3. No `sid` in metrics rows: does not hold. `.claude/tools/pipeline/metrics.py:27` writes `"sid": u.get("sid", "")` into every `ev: session` row, from `claude.py:395` (`session_id`).

**Actual gap (what does hold):**
- `ledger.metrics_sids` (`ledger.py:126-138`) is never called: 0 matches for `metrics_sids` and `sids=` under `.claude/`.
- Both callers pass no sids: `ledger.py:214` and `.claude/tools/retro/feature_retro.py:87` call `find_sessions(paths)`. So headless `<sid>.jsonl` logs are never added as sessions (`ledger.py:161-162` is dead for them).

**Counts:** 3 candidate causes checked; 2 do not hold, 1 holds in a different form (sids written but never read back). 0 machine counts.

Caveat: where the external `claude` CLI writes the file is not in the tree; the layout above is what the code assumes.

Could not find: any tree test or fixture that proves the real headless log path.
