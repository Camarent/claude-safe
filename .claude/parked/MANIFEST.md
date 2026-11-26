# Parked features

Parked = switched off on purpose, kept in the repo, with a way back. Nothing loads this folder. It is the only place parked work lives: no `old/`, `trial/` or `backup/` folders elsewhere.

Rules:
- One folder per parked feature: `parked/<name>/`. It holds the code, its tests and its notes.
- Every parked feature has one entry below with all four fields. An item without an entry is not parked; delete it (git history is the archive).
- A parked feature that should come back also gets one line in `practices/pipeline-backlog.md`.
- Dead experiments are deleted, not parked.

Entry template:

    ## <name>
    - what: one line
    - parked: YYYY-MM-DD, by whom
    - why: one line
    - restore: exact commands (git mv back, config key, tests to run)
    - revive when: the trigger that makes it worth reviving

## Currently parked

None.
