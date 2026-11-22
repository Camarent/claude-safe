"""Work folder layout (file and folder names), the PUBLISH line and entry keys, and the red-base message parts."""

WORK_DIR = ".work"  # per-feature / per-stage state folder under the workspace
SCRATCH_DIR = "tmp"  # the stage's scratchpad under WORK_DIR/<stage>: briefs name it; agents write temp files and logs only there
TASKS_FILE = "TASKS.md"  # a stage's task list inside WORK_DIR
PLAN_CONTEXT_FILE = "plan-context.md"  # the code leads a phase architect was given (written by cli_feature / plan_context)
PLAN_FILE = "Plan.md"  # a feature's plan inside WORK_DIR
PUBLISH_RE = r"^\s*PUBLISH\s*:\s*\S"  # the line naming artifact URLs to republish: in the request and in TASKS.md (driver republish step, plan check)
PUBLISH_LINE_RE = r"^\s*PUBLISH\s*:(.*)$"  # group 1 = the text after `PUBLISH:` (the URLs), matched per line
PUBLISH_KEY = "publish"  # the PROJECT_DEFAULTS key: {artifact_url: {"cwd", "build", "page", "files"}} (publish_entries)
PUBLISH_CWD, PUBLISH_BUILD, PUBLISH_PAGE, PUBLISH_FILES = "cwd", "build", "page", "files"  # keys of one publish entry
PUBLISH_OUT = "{out}"  # placeholder in a `build` string: replaced by the stage scratch dir (absolute, shell-quoted)
PUBLISH_NO_CHAIN = "&&"  # a `build` string must be one command: chaining is refused
EXIT_BLOCKED = 4  # exit code of plan.py / wave.py when a run stops blocked (back to the lead)
REQUEST_FILE = "request.md"  # copy of the feature's request prompt, written into its folder at plan time (plan check reads its PUBLISH line)
FETCH_FAIL_MARKS = (
    "Failed to consume the pack",
    "Failed to fetch advisory",
    "advisory-db",
    "Could not resolve host",
    "Connection timed out",
)  # gate output with one of these = a network/db fetch failure, not a real finding
FETCH_FAIL_LINE = "network/db fetch failed, retry"  # line added to a gate's output when only a fetch failure made it red
FETCH_RETRIES = 1  # extra runs of a gate whose red output is a fetch failure
BLOCKED_FILE = "BLOCKED.md"  # written into a stage/feature folder when a run stops blocked
PHASES_DIR, REPORTS_DIR, AUDITS_DIR, BASE_FILE = (
    "phases",
    "reports",
    "audits",
    "base.txt",
)  # feature folder layout; BASE_FILE = work branch head at a phase start
CARRIED_SECTION = "Carried from earlier phases"  # TASKS.md heading of a phase after the first: each earlier item is a task line or a `not needed: reason` line
CARRIED_NOT_NEEDED = "not needed:"
DEVIATION_TAG, DOCS_STALE_TAG = "DEVIATION", "DOCS-STALE"  # line starts in reports and audits that the next phase must read
DIGEST_LINES_CAP = 40  # open-item lines (FAIL, DEVIATION, DOCS-STALE, open FIXES) kept in a phase digest
DIGEST_TASKS_CAP = 60  # earlier task lines kept in a phase digest
DIGEST_STAT_CAP = 40  # `git diff --stat` lines kept per repo in a phase digest
DIGEST_CHARS_CAP = 12000  # hard cap of the whole digest text in a phase context
METRICS_LOG = "metrics.jsonl"  # a stage's session metrics inside WORK_DIR
ORCH_LOG = "orch.jsonl"  # a feature's or stage's event log inside WORK_DIR: its presence means the run has work (a resume)
ADDENDUM_HEADING = "## ADDENDUM {when}"  # heading of the block `retry --addendum` appends to a task file; {when} = ADDENDUM_TIME_FORMAT
ADDENDUM_TIME_FORMAT = "%Y-%m-%d %H:%M UTC"  # the {when} stamp of an addendum heading (UTC)
PREFLIGHT_MARKER = "preflight-green.json"  # inside the feature's WORK_DIR folder: {"head": sha} of the commit whose gates ran green
PREFLIGHT_MSG_MAX = 1500  # most characters of the red-base message printed
PREFLIGHT_DETAIL_LINES = 4  # output lines kept per failing gate in the red-base message
MISSING_BINARY_MARK = "must be installed"  # a failing gate line with this text and a configured binary name = a missing binary
MISSING_BINARIES_KEY = "missing_binaries"  # the PROJECT_DEFAULTS key above
RED_BASE_MISSING_LINE = "missing binaries: {names} (install them, then rerun)"  # red-base message line naming the missing binaries
RED_BASE_LAST_LINE = "blocked: base is red: {gate}; fix the base first"  # the last line of the red-base message
