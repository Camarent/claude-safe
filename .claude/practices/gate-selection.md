# Gate selection: the `Test:` contract

Read when writing a task `Test:` or `Done:` line. The architect follows this; the driver enforces the skip rules.

- A task `Test:` line selects narrow tests only. Two forms:
  - ptest file patterns: `scripts/dev.sh test test_ptest.py` (globs over file names in `.claude/tools/tests`, e.g. `test_ptest.py test_qrun*.py`).
  - pytest selectors for one test or `-k`: `scripts/dev.sh pytest .claude/tools/tests/test_ptest.py -k select`.
- The full suite (`scripts/dev.sh test` with no pattern, or `scripts/dev.sh pytest` alone) never appears in a task `Test:` line. The planner never lists it there.
- The full suite runs once, at the merge gate.
- The driver skips a command already green on the identical tree. Each skip is logged with the run id of the green run.
- Merge skips a gate only when the base is the same. A different base runs the gate again.
- Success measure: at most 2 full-suite runs per task.
- ptest patterns match file names only (`select_files`, `.claude/tools/ptest.py:101`). A pattern that matches nothing exits 1 with `ptest: no test files match`; it never passes silently. A pytest node id (`file.py::test`) is not a ptest pattern: use the pytest form above.
