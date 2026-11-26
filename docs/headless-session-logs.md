Headless session logs and the ledger.

**Where headless logs go:**
- Config dirs: `config_roots` in `.claude/tools/retro/ledger.py` returns `~/.claude/projects`, then `<CLAUDE_CONFIG_DIR>/projects` when that env var is set. `.claude/tools/retro/denials.py` reads the same variable. `find_transcript` searches every root in that order.
- Project folder name: the cwd with `/` and `.` replaced by `-` (`.claude/tools/pipeline/lead_context.py`). `find_transcript` globs `<root>/*/<sid>.jsonl`, so the folder name does not matter for lookup.
- File name: `<projects>/<folder>/<session_id>.jsonl`. Subagent logs sit under `<sid>/subagents/`.

**Session ids:** `.claude/tools/pipeline/metrics.py` writes `"sid"` into every `ev: session` row of `metrics.jsonl`. `metrics_sids` in `.claude/tools/retro/ledger.py` reads those ids back.

**How the ledger counts them:**
- `main` in `.claude/tools/retro/ledger.py` calls `metrics_sids` when `--metrics` is given and passes the ids to `build`, which passes them to `find_sessions`.
- `find_sessions` adds each top-level `<sid>.jsonl` whose id is in `sids` as a session of its own, because the driver's headless sessions have no `subagents/` dir.
- `.claude/tools/retro/feature_retro.py` calls `find_sessions(paths)` without sids, so the feature retro counts only sessions with a `subagents/` dir.

**Test fixtures:** `.claude/tools/tests/fixtures/headless_logs/` holds a `metrics.jsonl`, one listed headless log (`headless-sid-0001.jsonl`) and one log whose id is not listed (`unlisted-sid-0002.jsonl`). `test_ledger.py` checks that only the listed one is counted.

Caveat: where the external `claude` CLI writes the file is not in the tree; the layout above is what the code assumes.
