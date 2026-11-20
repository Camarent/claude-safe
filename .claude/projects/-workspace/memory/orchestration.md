---
name: orchestration
description: How the lead session delegates: no code edits by the lead, model choice, waves stop at the lead, scouts and ops for checks, prepare repos first
metadata:
  type: feedback
---

- **The lead does not edit code or tests**, not even one line to fix a red gate. Dispatch a dev agent with the gate in the brief, on a stage branch, then PR and squash-merge after green CI.
- **Models.** Pass `model` on every agent call (haiku for scout and ops needs no reason line; any other model needs a `MODEL-REASON:` line). Fixes, edits and propagation passes go to Sonnet; the most expensive model is only for new design phases, and only when the owner agrees. Never resume it for edits.
- **Waves hand back to the lead.** A wave with blocked or failed items stops and returns one summary. No automatic escalation ladder. Infra failures (harness crash, missing tool, no browser, approval wall) stop the stage on first occurrence: fix the cause or tell the owner. Never retry unchanged. The dev ladder (cheap model, one retry, stronger model, then lead) stays automatic; BLOCKED never feeds it.
- **Checks go to helpers.** Counting and status questions go to `scout` with a precise question; long or bulk runs go to `ops` with a task file. No hand-run one-off scripts in the lead session. Screenshots go to a scout, which returns text (at most about 30 lines); the lead opens an image only if the report is unclear.
- **Prepare a repo before a big job** (the prepare-repo skill, then the planner). Do not hand-edit first: it breaks the clean-tree check, and a repo without pipeline settings has no gates or audits. Never judge a job too mechanical for the pipeline.
- **Copy owner feedback (text and screenshots) into the stage's work folder before planning**; briefs cite those paths.
- **Judge the context step by** recall of modified files (bar 30%), time (ideal under 300 s, hard limit 600 s) and precision. Extra changed files are normal.
- **First search** uses the local find/nav tools; grep only to confirm literal text.

**Why:** a retry ladder once spent about ten dollars on three small tasks, and hand edits by the lead broke gates and the default branch.
**How to apply:** see [[git-and-release]] for merge rules and [[pipeline-notes]] for where lessons are written.
