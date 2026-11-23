"""pr-ready: the timeout of one gate command and the step names, and the CI-only line patterns ci_drift skips. The gate list itself is pipeline.json `gates` (+ `gates_merge`, `gates_final`), never copied here."""

import re

PR_GATE_TIMEOUT_S = 1800  # seconds one pr-ready gate command may run before the step is BLOCKED
PR_GATES_STEP = "pr-ready gates"  # step name in the pr-ready report and the BLOCKED text

# ci_drift: a CI run line that is never run locally (GitHub-only variables, secrets, expressions) is not drift.
CI_ONLY_RE = re.compile(r"\bRUNNER_\w*|GITHUB_\w*|secrets\.|\$\{\{")
# ci_drift: a bare shell setting (set/cd/export with arguments, or a plain VAR=value assignment) is not drift.
# Arguments never contain a shell separator, so `cd web && npm test` is not a bare setting.
CI_SHELL_SETTING_RE = re.compile(r"(?:set|cd|export)(?:\s+[^\s&;|]+)*|[A-Za-z_]\w*=[^\s&;|]*")

# pr-ready: a red CI-only line is reported with a `ci_drift_ignore` hint of its leading words (the prefix rule of ci_drift).
CI_HINT_WORDS = 2
# pr-ready: a red CI-only line whose output matches one of these is an environment problem (a variable not set), not a code failure.
ENV_OUTPUT_RE = re.compile(r"ConfigError|no config path|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+ (?:is )?(?:not set|missing|required)\b")
