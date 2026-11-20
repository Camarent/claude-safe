---
name: debugging-and-code
description: Diagnosis habits and code rules: recheck premises, comments as API docs, no literals in code, tests clean up, safe edits, remove stale files
metadata:
  type: feedback
---

**Diagnosis**
- A failed fix is a trigger to re-derive: ask which assumption the failure disconfirmed. Do not go deeper down the same branch.
- Offer the fix that works under every candidate cause before committing to one guessed cause. If the obstacle can be removed, remove it instead of working around it.
- A blocked pipeline step: find the cause, reproduce the failing check by hand, fix the tool or config, add a regression test, then retry. A plain retry almost never fixes it. Hand merges and skips are workarounds too: log the cause and fix the pipeline in the same pass.
- "Check" or "test" a change means a smoke run on real data now, reported as seen. Unit tests use synthetic fixtures and never depend on one repo's content, a stage sequence or agent names.

**Code**
- Comments and docstrings are public API docs: what it returns, what arguments mean, the fallback contract. No rationale essays, rejected alternatives or worked edge cases. Default to no comment. If a decision needs defending, encode it as a test whose name is the claim; rationale belongs in the commit or PR.
- No literals for branch names, paths, timeouts, tool lists or flags in pipeline code: named settings in the configuration package with an override file or env var. Tests clean up their temp dirs, files and processes (`addCleanup`, `TemporaryDirectory`).
- Regex edits are unsafe: anchor, assert the changed-line count, test with a decoy sharing the value. Prefer a parser for JSON, TOML and YAML.
- Prefer the clean, reusable design over a workaround or a convention hidden in text. Format changes stay additive and tested.
- Remove retired and stale files in the same pass that retires them (old plan tasks, dead locks, temp files). Do not call leftovers harmless; report what was removed.

**Why:** these repeated mistakes cost the owner time; leaked test temp dirs once exhausted tmpfs inodes.
**How to apply:** see [[git-and-release]] for commit rules and [[orchestration]] for who edits code.
