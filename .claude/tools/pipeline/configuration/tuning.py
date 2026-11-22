"""Named settings of the stage's tuning knobs: error patterns that mark an environment failure, repair and rerun caps, gate skip paths, slice sizes, rework and review limits, and the names of the state files the stage writes. One owner: other modules import these from pipeline.configuration."""

ENV_ERROR_PATTERN = r"No module named|ModuleNotFoundError|no config path|command not found"  # regex: a failing output that names a missing environment piece (the repair step may fix it)
ENV_REPAIR_MAX = 1  # how many environment repair attempts a failing step gets before it is reported
STALE_RERUN_MAX = 2  # how many times a stale failing report is rerun before the lead decides
DOC_PATHS = ("*.md",)  # changed-file globs of docs files
GATE_SKIP_PATHS = DOC_PATHS  # changed-file globs that never trigger a task gate (docs only)
DOC_CITE_RE = (
    r"`(?P<path>[^`\s]+\.[A-Za-z0-9]+):(?P<line>\d+)`\s*\"(?P<text>[^\"]+)\""  # regex per line: a `path:line` cite followed by the quoted line text it names
)
LEAD_SLICE_CHARS = 6000  # chars of code slices a brief carries under the code leads (no Pattern file named)
LEAD_SLICE_CHARS_WITH_PATTERN = 3000  # chars of a brief the lead reads when a pattern file is named
ESCALATE_DRAFT_CHARS = 4000  # chars of each Sonnet draft file (Plan.md, TASKS.md) the Opus plan rerun is handed
HAIKU_REWORK_TRIES = 1  # rework rounds a Haiku dev gets before the task escalates
LEAD_NOTE_CHARS = 300  # chars of a lead retry note kept in the brief when the report path is passed
GATE_OUTPUT_CHARS = 1500  # chars of the driver test-run output (the tail) the reviewer brief carries
REVIEW_DOCS_MAX_TURNS = 12  # `--max-turns` of a docs review session
DENIAL_LOG_NAME = "denials.log"  # file in the stage folder that collects permission denials of headless sessions
PGID_FILE_NAME = "wave.pgid"  # file in the stage folder that holds the process group id of a running wave driver
SQUASH_MSG_FILE = "SQUASH_MSG"  # file in the git dir that git writes on `merge --squash`; a leftover one means an interrupted squash
STOP_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")  # signals that stop the wave driver: kill every live session group, then exit
INIT_PID = 1  # a driver running as this pid (container init) does not take SIGHUP as a stop: the hangup stays optional
MERGE_RESIDUE_FILE = "merge-residue.txt"  # file in the stage folder that lists merge leftovers a later merge must clean up
