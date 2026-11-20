---
name: pipeline-notes
description: Where pipeline lessons and notes live, and how pipeline files are written (English, compact)
metadata:
  type: feedback
---

- **Pipeline files are English and compact.** The global CLAUDE.md holds only rules every session needs. Orchestration detail lives in ORCHESTRATOR.md (read when running a plan), and rare lessons go to `practices/*.md` (read on demand). Agents never read ORCHESTRATOR.md. Static files are paid on every turn and in every subagent, so write a new lesson short.
- **Pipeline notes versus project notes.** Notes, handoffs and backlog items about the pipeline itself (tools, agents, context step, retro, eval) go to `practices/`, with a pointer in ORCHESTRATOR.md. Facts and backlog of the project being built stay in that project's own notes. Before writing a note, ask which of the two it is.
- **Scratch files for pipeline experiments** go to `parked/` or the scratchpad, never into the project working folder.
- **Project lessons stay out of this memory.** A lesson tied to one project's data or format belongs in that project's indexed notes, not here.

**Why:** the owner moved pipeline notes out of a project folder more than once.
**How to apply:** see [[orchestration]].
