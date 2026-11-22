"""pr-ready: the timeout of one gate command and the step names. The gate list itself is pipeline.json `gates` (+ `gates_merge`, `gates_final`), never copied here."""

PR_GATE_TIMEOUT_S = 1800  # seconds one pr-ready gate command may run before the step is BLOCKED
PR_GATES_STEP = "pr-ready gates"  # step name in the pr-ready report and the BLOCKED text
