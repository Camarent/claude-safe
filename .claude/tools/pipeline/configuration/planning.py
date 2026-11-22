"""Plan and task rules: task size caps, per-file task phrases, dry-run fields and extension lists."""

from .limits import EV_DISPATCH, EV_SKIP
from .paths import QRUN_WRAPPER

TASK_FILES_WARN = 15  # predicted files above this: `size:` warning and the dev starts on Sonnet
STARTED_EVENTS = (EV_DISPATCH, EV_SKIP)  # orch.jsonl events that mark a task as started (plan.py task --before never renumbers or delays such a task)
TASK_FILES_FAIL = 25  # above this plan_check fails: split the task by seam
JQ_FILE_MAX_BYTES = 1_000_000  # brieflint reads a json file for its jq key check only up to this size
GIT_ENTRY = ".git"  # a dir holding this entry (dir or file) is a git repo root: graph._has_job_branches
PER_FILE_PHRASES = (  # heuristic: a task whose Goal/Done text matches one of these regexes decides something file by file (plan_rules.per_file_problems)
    r"(?i)\bfor (?:each|every) (?:[\w./*-]+ ){0,2}(?:file|module|page|entry|record)s?\b",
    r"(?i)\b(?:decide|decision)s? (?:\w+ )?per[- ](?:file|module|page|entry)\b",
    r"(?i)\bper[- ]file\b",
    r"(?i)\beach (?:\w+ )?file in\b",
    r"(?i)\b(?:every|all) files? (?:in|under|of|matching)\b",
)
PER_FILE_CHECK_PHRASES = (
    "undecided",
    "every file",
    "each file",
    "all files",
    "for f in",
    "xargs",
    "find ",
)  # a `Test:` line holding one of these counts as a per-file machine check
OUTPUTS_FIELD = "Outputs"  # task line `Outputs: a, b`: one expected file or count per input of a per-file task
TOOL_FIELD = "Tool"  # task line `Tool: path args`: a script the task runs over data
DRYRUN_FIELD = "Dry run"  # task line `Dry run: <cmd>  # expect > N changes`
DRYRUN_FLAGS = ("--dry-run", "--dry", "--check", "--preview")  # a script Predicted path used with one of these needs a `Dry run:` line
SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".py", ".sh")
DRYRUN_COUNT_RE = r"(?i)\b(\d+)\s+(?:changes?|changed|updates?|updated|records?|files?|matches|matched|rewrites?|rewritten)\b"  # first hit in the dry-run output is the effect count
DRYRUN_TIMEOUT_S = 120  # one `Dry run:` command in the temp worktree
DRYRUN_SHELL = ("sh", "-c")
TASK_DIR_WEIGHT = 5  # a Predicted directory or glob entry counts as this many files for the size cap
DONE_WHEN_EXTS = frozenset(  # a bare name (no `/`) in `done_when` is taken as a file only with one of these extensions
    (
        "py",
        "js",
        "mjs",
        "cjs",
        "ts",
        "tsx",
        "jsx",
        "json",
        "md",
        "txt",
        "yaml",
        "yml",
        "toml",
        "sh",
        "rs",
        "go",
        "html",
        "css",
        "csv",
        "log",
        "xml",
        "ini",
        "cfg",
        "lock",
        "sql",
    )
)
CMD_WRAPPERS = (
    QRUN_WRAPPER,
    "qrun.py",
    "python3",
    "python",
    "node",
    "bash",
    "sh",
)  # command words put before the real executable of a done_when / Test / gate command; plan_commands strips them (matched by basename)
CMD_WRAPPER_OPTS = ("--", "-I", "-u", "-B")  # option words right after a wrapper (`cs-qrun --`, `python3 -I`); also stripped
CMD_OUTPUT_OPTS = ("-o", "--out", "--output")  # the word after one of these is an output file the command makes, not a checked path
CMD_BUILD_DIRS = ("target", "dist", "build", "out", "node_modules", ".uv")  # first path part of a file a build makes: never flagged as missing
CMD_SUGGEST_DEPTH = 3  # a bare argument may name a directory at most this many path parts deep (`did you mean`)
CMD_REF_TOOLS = ("git", "gh")  # version-control and forge CLIs: their arguments are refs, revisions or ids, never files; only the executable is checked
CMD_REF_MARKERS = ("..", "~", "^", "@{")  # an argument holding one of these is a revision (`HEAD~1`, `master..dev`, `HEAD^{tree}`), never a path
CMD_REF_PREFIX = "refs/"  # an argument starting with this is a full ref name (`refs/tags/v0.1.0`), never a path

SHARED_MIN = 3  # a file listed by this many tasks of one stage: `shared:` warning, `shared-file:` error for a data or registry file (plan_check)
REGISTRY_WORDS = frozenset(("ids", "lock", "registry", "edited", "list"))  # whole name tokens that mark an append-only registry file
TEST_NAME_RE = r"(?i)(?:^|[._-])(?:tests?|spec)(?:[._-]|$)"  # a test file name is never a registry
TEST_ENV_WORDS = (
    "chrome",
    "chromium",
    "puppeteer",
    "playwright",
    "--remote-debugging",
    "headless",
)  # a Test:/Done command with one of these needs a live browser
TEST_ENV_MAX_FILES = 5  # test files scanned per task for gitignored paths
TEST_ENV_MAX_BYTES = 200_000  # bytes read per scanned test file

NEG_GREP_RE = (
    r"(?:!\s*(?:\w+\s+)?grep\b|\bgrep\s+-\w*[Lv]\w*\b)"  # a negative grep in a Done/Test line: `! grep`, `grep -L`, `grep -vq` (plan_rules.check_negative_tag)
)
MARKUP_TAGS = (
    "script",
    "style",
    "div",
    "span",
    "a",
    "img",
    "link",
    "meta",
    "iframe",
    "form",
    "input",
    "button",
    "table",
    "body",
    "head",
    "html",
    "svg",
    "canvas",
    "video",
    "audio",
    "section",
    "nav",
    "ul",
    "li",
    "p",
)  # tags whose bare ban the `neg:` warning names (a generic `<word` is also code: `Vec<String`)
BARE_TAG_RE = (
    r"<(?:" + "|".join(MARKUP_TAGS) + r")\\?[\"']"
)  # a markup tag fragment that ends at the closing quote, escaped or not: `"<script"`, `\"<div\"` (no attribute, no `>`)
WEBTEST_WARN_PREFIX = "webtest:"  # warning prefix of plan_rules.check_webtest_type; plan_check.errors_of never fails on it
PREREQ_WARN_PREFIX = "prereq:"  # warning prefix of plan_rules.check_unlisted_prereq; plan_check.errors_of never fails on it
AFFECTED_WARN_PREFIX = "tests-use:"  # warning prefix of plan_rules.check_affected_tests; plan_check.errors_of never fails on it
SCOPE_MODES = (
    "off",
    "warn",
    "strict",
)  # pipeline.json `scope_guard`: off = no check; warn = the reviewer is told of files outside the predicted paths; strict = the reviewer must fail the task unless its text justifies each one
SCOPE_ALLOW_ALL = (
    "tests",
    "docs",
    "notes",
)  # pipeline.json `scope_allow`: kinds of file outside the predicted paths that are no finding (tests: a test file whose path holds a predicted file's stem; docs: under `docs_dir` or a .md file; notes: under SCOPE_NOTES_DIR)
SCOPE_NOTES_DIR = ".notes"  # folder of project notes (kind `notes` of `scope_allow`)
SCOPE_STEM_MIN = 3  # a predicted file's name stem shorter than this ties no test file to it
SCOPE_TEST_RE = r"(?i)(?:^|[/._-])(?:tests?|spec)(?:[/._-]|$)"  # a test path
SCOPE_SHOWN = 12  # files named in the scope note
SCOPE_STRICT_NOTE = (
    " Scope check (must address): the job changed files outside the task's predicted paths and the task text does not name them: {files}. "
    "Judge each file: FAIL only for a side refactor; a file the task or a red gate needs is fine. Name every such file in your report."
)
SCOPE_HINT_NOTE = " Predicted paths are a planning hint: a file outside them is never a FAIL by itself."  # reviewer brief, every review
SCOPE_WARN_NOTE = " Scope note: the job changed files outside the task's predicted paths and the task text does not name them: {files}. Name them in your report; this alone is not a FAIL."
TESTCMD_WARN_PREFIX = "testcmd:"  # warning prefix of plan_testcmd.check_bare_test_runner; plan_check.errors_of never fails on it
TESTCMD_HINT_KEY = "test_command_hint"  # pipeline.json: the project's real test command (what a `Test:` line should run instead of bare pytest); when unset a `gates` command that runs pytest through a wrapper is used
BARE_RUNNER_RE = r"^(?:pytest|py\.test|(?:python[\d.]*)(?:\s+-\w+)*\s+-m\s+pytest)(?:\s|$)"  # a command that starts pytest with no wrapper
AFFECTED_MIN_STEM = 4  # a file name stem shorter than this (`io`, `app`) matches too much to say which tests use it
AFFECTED_MAX_TESTS = 10  # more tests than this mention one name: it is too common to say which assert the old behaviour, no warning
AFFECTED_CODE_EXTS = (
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".py",
    ".rs",
)  # only code files and code tests are matched by name (data, docs and styles are named by far too many tests)
AFFECTED_TEST_DIRS = {"test", "tests"}  # a file under a directory of this name is a test file
AFFECTED_SHOWN = 5  # tests named in one `affected:` line
NEG_WARN_PREFIX = "neg:"  # warning prefix of that rule; plan_check.errors_of never fails on it
GENERATED_MARKER_KEY = "generated_marker"  # pipeline.json: regex; a tracked file whose first GENERATED_HEAD_LINES lines match it is a generated file
GENERATED_MARKER_RE = r"(?i)generated by|do not edit"  # default of `generated_marker`
GENERATED_HEAD_LINES = 5  # lines at the top of a file searched for the generated marker
GENERATED_WARN_PREFIX = "generated:"  # warning prefix of plan_rules.check_generated_paths; plan_check.errors_of never fails on it
