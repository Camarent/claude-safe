"""Plan check problems: a str per problem line that also carries its kind (the leading `kind:` token) and its level (error or warn).

A Problem equals its text, so every list of problem strings stays valid; plan_check.errors_of reads the level from KIND_LEVELS.
"""

import re

ERROR = "error"  # a problem: `cs-plan check` fails
WARN = "warn"  # a warning line: never fails a check

# kind -> level. A kind missing here is an error (plan_check.errors_of never drops an unknown line).
KIND_LEVELS = {
    # warnings: the prefixes errors_of used to filter out
    "combine": WARN,
    "size": WARN,
    "shared": WARN,
    "gate": WARN,
    "release": WARN,
    "done": WARN,
    "cmd": WARN,
    "open-edit": WARN,
    "repos": WARN,
    "ux-flows": WARN,
    "parallel": WARN,
    "tests-use": WARN,
    "neg": WARN,
    "skip-gate": WARN,
    "prereq": WARN,
    "webtest": WARN,
    "generated": WARN,
    "research-hint": WARN,  # planned: plan warning from task-10
    "reach": WARN,  # a Mounts: header whose Done names no entry proof (plan_wiring)
    # errors
    "publish": ERROR,
    "ci-drift": ERROR,
    "shared-file": ERROR,
    "test-env": ERROR,
    "ux-vars": ERROR,
    "dryrun": ERROR,
    "testrun": ERROR,
    "flags": ERROR,
    "plan-review": ERROR,
    "docs-wave": ERROR,
    "docs-count": ERROR,
    "testban": ERROR,
    "qrun-op": ERROR,
    "outputs": ERROR,
    "research": ERROR,
    "pins": ERROR,
    "testcmd": ERROR,  # a bare pytest Test line where the project names its test command (was a warning)
    "ignored-done": ERROR,  # planned: task-5
    "cite-short": ERROR,  # planned: task-7
    "carried-notes": ERROR,
    "wiring": ERROR,  # a new module under a wiring dir with no mount (plan_wiring)
    "tool-block": ERROR,  # a Test/Done names a JS tool whose smoke probe fails (plan_toolblock)
    "knip-prod": ERROR,  # a Test/Done runs knip --production with no knip entry key (plan_toolblock)
}

KIND_RE = re.compile(r"([a-z][a-z-]*):")  # a problem line starts with this token


class Problem(str):
    """A problem line: equal to TEXT; KIND is its leading `kind:` token, LEVEL is ERROR or WARN."""

    kind: str
    level: str
    text: str

    def __new__(cls, kind: str, level: str, text: str) -> "Problem":
        obj = super().__new__(cls, text)
        obj.kind = kind
        obj.level = level
        obj.text = text
        return obj

    @classmethod
    def parse(cls, text: str) -> "Problem":
        """-> the Problem of TEXT: the kind is the leading `kind:` token ('' when there is none), the level comes from KIND_LEVELS (unknown kind: ERROR)."""
        m = KIND_RE.match(text)
        kind = m[1] if m else ""
        return cls(kind, KIND_LEVELS.get(kind, ERROR), text)
