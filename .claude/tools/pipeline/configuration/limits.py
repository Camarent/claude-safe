"""Limits, timeouts, caps and event names of the pipeline (one named value each)."""

import os

from pipeline import cmdnames

from .layout import WORK_DIR
from .paths import _cs_index
from .verdicts import MODEL_HAIKU, MODEL_SONNET

# limits
MAX_ROUNDS = 2  # failed review rounds before the lead is asked
MAX_TRIES = 4  # runs of one role (dev, reviewer) per task, any model; a fifth goes to the lead
REVIEW_EXTRA_ROUNDS = 1  # extra dev rounds after the last failed review round (MAX_ROUNDS), each with the verbatim findings; 0 = the lead is asked at once
EXTRA_ROUND_WHY = "extra review round: the dev reworks the verbatim text of every failed review"  # dispatch reason of an extra dev round (counted from the log)
NIT_PASS_WHY = "nit-only review FAIL after the failed rounds: PASS with the nits as notes"  # nosec B105  # a log reason text, not a password; reason of the PASS report that closes such a task (its file names the nits)
REVIEW_SKIP_RC = 3  # `lp review` exit code for "skipped: diff too big"; callers treat it as no review, never as FAIL
LOCAL_CONTEXT_TOKENS = 131072  # the Strata launch `--max-context`: one request must fit it
LOCAL_ANSWER_TOKENS = 5000  # thinking + answer budget of a pre-review call
LOCAL_PROMPT_OVERHEAD_TOKENS = 4000  # rules, criteria and brief around the reviewed text
EST_ASCII_CHARS_PER_TOKEN = 3.5  # pipeline.util.est_tokens: ASCII text; any other character counts as one token (conservative)
PREREVIEW_INPUT_TOKENS = min(
    int((LOCAL_CONTEXT_TOKENS - LOCAL_ANSWER_TOKENS - LOCAL_PROMPT_OVERHEAD_TOKENS) * 0.85), 40_000
)  # est. tokens one pre-review call may send; start conservative; raise after timing real inputs (5k/20k/50k/90k tokens) on an idle model; the computed figure (window - answer - prompt - 15% safety, 103762) is the hard ceiling; more is SKIPPED, never chunked
PREREVIEW_DIFF_CHARS = int(
    PREREVIEW_INPUT_TOKENS * EST_ASCII_CHARS_PER_TOKEN
)  # prereview.py: diff chars read per repo from the job worktree: a longer one is over the token budget anyway, so a cut diff is skipped, not reviewed half
PREREVIEW_FINDINGS_MAX = 20  # findings one pre-review call may report
PREREVIEW_LP_TIMEOUT_S = (
    900  # prereview.py: one `lp review`/`lp judgefiles` call: slot wait + a ~100k-token prefill (the model request itself times out at 600 s in askmodel)
)
PREREVIEW_GIT_TIMEOUT_S = 60  # prereview.py: `git diff HEAD` of an uncommitted task
PREREVIEW_FILE_EXTS = (".md", ".txt", ".tsv", ".csv", ".json", ".jsonl")  # prereview.py file mode: deliverable files it judges (scripts and tests are not)
REASON_CUT = 200  # prereview.py: chars of an lp review note kept in a skip or unclear reason
DIFF_SHA_LEN = 16  # prereview.py and steps.py: hex chars of the diff hash that marks an unchanged diff
PIPELINE_EXCLUDES = (
    "pipeline.json",
    "AUDIT.md",
    ".pipeline/",
    ".work/",
    ".wt/",
    ".audits/",
    ".code-index/",
    ".notes/",
)  # prepare_repo.py: local pipeline files, never tracked
EXCLUDE_FILE = "info/exclude"  # prepare_repo.py: path under the git dir, resolved with `git rev-parse --git-path`
GITIGNORE_FILE = ".gitignore"  # prepare_repo.py: the repo's tracked ignore file, gets the same entries as the exclude file
LANG_IGNORES = (
    (("pyproject.toml", "setup.py", "requirements.txt"), ("__pycache__/", "*.pyc", ".venv/")),
    (("package.json",), ("node_modules/", "dist/")),
    (("Cargo.toml",), ("target/",)),
)  # prepare_repo.py: marker files in the repo root -> .gitignore lines for that language (only detected languages get lines; untracked build output stops the dirty-tree preflight)
AUDIT_TOOLS_TAG = "audit-tools"  # prepare_repo.py: info string of the AUDIT.md fenced block checked for malformed lines
AUDIT_TOOLS_MIN_FIELDS = 4  # `name | cwd | command | parser` (audit/audit_run.py rejects fewer)
WORKTREES_KEY = "worktrees"  # prepare_repo.py: pipeline.json key reported (and added with --add-worktrees) when missing
PIPELINE_JSON = "pipeline.json"  # prepare_repo.py: project settings file name
CLAUDE_FILE = "CLAUDE.md"  # prepare_repo.py: workspace notes file that may get the `## Docs` section
DOCS_HEADING = "## Docs"  # heading final-docs looks for in CLAUDE_FILE
DOCS_SECTION_TEMPLATE = "{heading}\n\nDocs folder: `{dir}/`\n"  # prepare_repo.py: the section written when the repo has a docs folder
GUIDELINES_HEADING = "## Guidelines"  # heading of the CLAUDE.md section that points agents at the language guidelines
GUIDELINES_SECTION_TEMPLATE = "{heading}\n\n{lines}\n"  # prepare_repo.py: the section written for the detected languages
GUIDELINES_LANG_LINE = "{lang}: read {dir}/{lang}.md before editing {lang} files"  # one line per detected language
GUIDELINES_BASE_LINE = "all: {dir}/base.md"  # always listed
GUIDELINES_RUNBOOK_LINES = (
    "driver: read {dir}/driver-runbook.md when you watch a running pipeline driver, when it blocks, or when you wake up to check a stage",
    "lead: read {dir}/lead-rules.md when you change the pipeline, plan a pipeline fix, or sync installed pipeline config",
)  # prepare_repo.py: practices links appended to the guidelines section ({dir} = PRACTICES_DIR); one line per file
LANG_MARKERS = (
    ("cpp", ("CMakeLists.txt", "meson.build", "*.cpp", "*.cc", "*.hpp"), ("", "src")),
    ("rust", ("Cargo.toml",), ("",)),
    ("typescript", ("tsconfig.json",), ("",)),
    ("js", ("package.json",), ("",)),
    ("python", ("pyproject.toml", "setup.py", "requirements.txt"), ("",)),
    ("csharp", ("*.csproj", "*.sln"), ("",)),
)  # guidelines.py: language slug -> (file name patterns, repo subfolders searched, top level only); "" = the repo root
LANG_EXTENSIONS = (
    ("cpp", (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")),
    ("rust", (".rs",)),
    ("typescript", (".ts", ".tsx")),
    ("js", (".js", ".jsx", ".mjs", ".cjs")),
    ("python", (".py",)),
    ("csharp", (".cs",)),
)  # guidelines.py digest: language slug -> changed file extensions that select its <lang>.md
GUIDELINES_EXT = ".md"  # guidelines.py digest: one file per language and base.md, all in GUIDELINES_DIR
BASE_GUIDELINE = "base.md"  # guidelines.py digest: the guideline file that applies to every change
GUIDELINES_DIGEST_MAX_LINES = 60  # guidelines.py digest: most lines digest() returns, pointers included
TOOL_PROBE_TIMEOUT_S = 30  # prepare_repo.py: one `<python> -c "import pytest"` probe
SHELL_KEYWORDS = frozenset(
    {"if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done", "case", "esac", "in", "function", "select", "time", "!", "{", "}", "(", ")"}
)  # prepare_checks.py: shell words that are not a program to look for on PATH
SHELL_BUILTINS = frozenset(
    {
        "cd",
        "export",
        "set",
        "unset",
        "test",
        "[",
        "[[",
        "source",
        ".",
        "echo",
        "true",
        "false",
        "exit",
        "exec",
        "eval",
        "pushd",
        "popd",
        ":",
        "read",
        "ulimit",
        "umask",
        "alias",
        "type",
        "command",
        "wait",
        "trap",
        "return",
    }
)  # prepare_repo.py toolchain check: first words that need no binary on PATH
PYTHON_MODULE_PROBES = ("pytest",)  # prepare_repo.py: `<python> -m <module>` in a gate -> that interpreter must import the module
PATH_HINT = "put it on the driver's PATH (install it there, or `export PATH=<its folder>:$PATH` before starting the driver)"  # prepare_repo.py: how to fix a missing tool
PREREVIEW_SKIP_REVIEWER, PREREVIEW_SKIP_ASK = "reviewer", "ask_main"  # values of the `prereview_skip` setting
ENV_PREREVIEW_SKIP = "PIPELINE_PREREVIEW_SKIP"  # env override of `prereview_skip`
PREREVIEW_FREE_ROUNDS = 2  # pre-review FAILs per task that go back to the dev without costing a dev try; after that the paid reviewer runs
GATE_FREE_ROUNDS = 2  # task gate runs per task that may go red and back to the dev without costing a dev try; the next red goes to one Sonnet dev run, a red after that to the lead
HAIKU_DEV_TRIES = 2  # Haiku dev runs per task before the dev moves to Sonnet
SESSION_TIMEOUT_S = 1800.0  # one headless session (env WAVE_TIMEOUT overrides)
GIT_LONG_TIMEOUT_S = 120  # one git call that may merge or check out (pipeline/worktree.py)
GIT_SAFE_DIR = "*"  # the one safe.directory value, as git -c args and as env below
GIT_SAFE_DIR_ARGS = ("-c", f"safe.directory={GIT_SAFE_DIR}")  # repos may be owned by another uid (GIT_SAFE_ENV is the same setting for the environment)
GIT_TIMEOUT_S = 60  # one git call of the pipeline (plan_check.py, pr_mode.py, qualityrun.py use it)
CODESRV_REPLY_TIMEOUT_S = float(
    os.environ.get("CODESRV_REPLY_TIMEOUT", "300")
)  # local.py find|index waits this long for the warm server, then restarts it once and falls back to in-process
CODESRV_REQUEST_LIMIT_S = float(
    os.environ.get("CODESRV_REQUEST_LIMIT", "600")
)  # server watchdog: a request running longer ends the server (the next call starts a fresh one)
CHECKOUT_LOCK = "checkout.lock"  # per main checkout (all stages on it): in its git dir, or in WORK_DIR when not a git checkout
STAGE_LOCK = "wave.lock"  # per stage, in its WORK_DIR folder: the driver holds it (flock) and writes its pid; wt.py merge reads the pid
LOCK_RETRY_MAX = 5  # cli_wave._take_lock: how many times a driver retries the flock while a probe (other_driver) holds it shared for a moment
LOCK_RETRY_S = 0.05  # cli_wave._take_lock: seconds between those retries
INDEX_TIMEOUT_S = 900.0  # one `local.py index` run of index_step (ORCH_SCRIPT_TIMEOUT overrides)
CI_EMPTY_POLLS = 3  # fallback CI wait: reads that may return no run (not registered yet) before "no checks" is believed, when the repo has workflows
CI_RUNS_LIMIT = 50  # fallback CI wait: workflow runs read per commit
CI_DENIED_MSG = "Resource not accessible by personal access token"  # gh stderr when the token cannot read check runs (fine-grained tokens have no checks:read)
GATE_GROUP_MAX_WORKERS = 4  # final_check: commands of one `parallel` group that run at the same time; a bigger group runs in waves
GATE_WATCH_MAX_ENTRIES = 5000  # audit/gatewatch.py: files recorded per snapshot of a `gate_watch` folder (a bigger folder is cut and the report says so)
GATE_WATCH_GREP_FILES = 50  # audit/gatewatch.py: most files that name a changed path that are read to say which path they name
GATE_WATCH_FILE_BYTES = 200_000  # audit/gatewatch.py: bytes read of each such file
GATE_WATCH_SHOW = 10  # audit/gatewatch.py: changed paths one gate report lists
GATE_ENV_NAMES = ("TZ", "TMPDIR", "HOME", "PATH")  # a red task gate report lists these environment variables ...
GATE_ENV_PREFIXES = ("GIT_", "WT_")  # ... and every variable that starts with one of these (plus the names in the project's `test_env`)
SCRIPT_TIMEOUT_S = 1800.0  # one no-model script step (audit_run, final_check, ux_run ...): killed after this
AGENT_TOOL_TIMEOUT_S = 600  # the agent Bash tool kills a command after this (Claude Code maximum, 600000 ms)
GATE_TIMEOUT_S = AGENT_TOOL_TIMEOUT_S - 60  # qrun and testrun kill a gate after this: below AGENT_TOOL_TIMEOUT_S, so the agent gets the output line
MUTATION_GATE_TIMEOUT_S = 300  # qrun kills one gate run of a reviewer mutation check (ENV_GATE_KIND=mutation) after this: a mutation run is one test, not the full gate; never above GATE_TIMEOUT_S
GIT_NETWORK_TIMEOUT_S = 300  # one git push / fetch (pipeline/pr_ops.py)
GIT_QUICK_TIMEOUT_S = 30  # one cheap git read (configuration.gitbranch._git_out, audit_run rev-parse)
LOCAL_FIND_TIMEOUT_S = 120  # one `local.py find` call of the brief builder
ACCEPTANCE_STEP = "acceptance"  # driver step id: runs the Plan.md `- cmd:` lines in a fresh worktree of the stage branch (pipeline/acceptance.py)
ACCEPTANCE_REVIEW_FILE = "manual-review.md"  # written by the acceptance reviewer agent to .work/<stage>/: one `MANUAL n: ...` line per item
ACCEPTANCE_REVIEW_AGENT = "reviewer"  # agent that checks the MANUAL items
ACCEPTANCE_FILE = "acceptance.md"  # written to .work/<stage>/: one line per checklist item
ACCEPTANCE_CMD_TIMEOUT_S = 900  # one acceptance command (qrun kills its process group at this limit)
ACCEPTANCE_TAIL_LINES = 3  # last output lines kept per command in acceptance.md
ACCEPTANCE_TIMING_FLAG_KEY = "acceptance_timing_flag"  # pipeline.json: flag appended to the project test command in the acceptance command lines
ACCEPTANCE_TIMINGS_LOG = "acceptance-timings-{n}.log"  # full output of each flagged acceptance command, in the stage reports folder
RETRO_DIR = "retro"  # under .work/<stage>/: the retro inputs the pipeline writes (pipeline/slow_suite.py)
SLOW_SUITE_FILE = "slow-suite.jsonl"  # .work/<stage>/retro/: one row per acceptance command that hit ACCEPTANCE_CMD_TIMEOUT_S
SLOW_SUITE_KIND = "slow-suite"  # `kind` of a row of SLOW_SUITE_FILE
ACCEPTANCE_CMD_TAG, ACCEPTANCE_EYES_TAG = "cmd:", "eyes:"  # tags of a Plan.md checklist line
ACCEPTANCE_PROSE_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "should",
        "must",
        "will",
        "shall",
        "does",
        "has",
        "have",
        "passes",
        "pass",
        "works",
        "shows",
        "contains",
        "exists",
        "that",
        "with",
        "all",
    }
)  # sentence words: a checklist `cmd:` with two of them reads as a sentence
ACCEPTANCE_PROSE_OPENERS = ACCEPTANCE_PROSE_WORDS | {
    "verify",
    "ensure",
    "confirm",
    "every",
    "no",
    "check",
}  # a checklist `cmd:` starting with one of these reads as a sentence
ACCEPTANCE_MANUAL = "MANUAL"  # acceptance.md status of an `eyes:` line (not run)
LONG_CMD_TIMEOUT_S = 900  # one long command: done_when check, ux screenshots, extra-tool run, localdev test run
SLOT_TIMEOUT_S = 3600  # wait for a local-model queue slot (search/localq.py)
TRIGGER_ESCALATION_PCT = 25  # triggers.py: more of the tasks escalated to Sonnet than this share (percent) is a retro trigger
TRIGGER_FIX_SHARE_PCT = 20  # triggers.py: more fix tasks than this share (percent) of the base tasks is a retro trigger
WAVE_UNLIMITED = 10**6  # cli_wave dry loop: stand-in for "no --max-dispatches / --max-waves"
WAVE_STEP_CAP = 500  # cli_wave dry loop: steps before it stops whatever the plan does
JOB_DIFF_MAX_CHARS = 14000  # a job's diff handed to a reviewer is cut at this many characters
LINK_WRITE_SHOWN = 5  # files named in an outside-write note
LINK_WRITE_PATCH = "linkwrite-{task}-{stamp}-{repo}.patch"  # the reverted outside write, kept in the stage's work folder (one per repo; a retry never overwrites an earlier one)
ADD_REPO_CMD = (
    "{tool} add-repo --stage {stage} --task {task} --dir {dir} --repo"  # the command a dev runs to get a real worktree of a nested repo; the repo is appended
)
LINK_BASE_FILE = "linkbase.json"  # per job (next to .branch): the state of the linked main repos when the dev started
LINK_GIT_MERGE_FILES = ("MERGE_HEAD", "SQUASH_MSG")  # a repo with one of these in its git dir is mid-merge: its staged changes are not a dev's write
MAIN_BASE_FILE = "mainbase.json"  # per job (next to .branch): the dirty state of the main checkout when the dev started
MAIN_GUARD_IGNORE = (
    ".wt",
    WORK_DIR,
)  # top-level folders of the main checkout that are the pipeline's own (job worktrees, stage work files), never a dev's write
MAIN_GUARD_IGNORE_GLOBS = ("qrun-*.log",)  # path globs of the main checkout the guard never reports; pipeline.json key main_guard_ignore_globs overrides
MAIN_DIRT_PREFIX = "MAIN-DIRT:"  # first word of the note maincheck.check returns; the driver tells it from a dev's own failure
MAIN_WRITE_NOTE = (
    MAIN_DIRT_PREFIX + " files in the MAIN checkout changed or appeared while this task ran ({files}, +{more} more). "
    "The dev cannot fix this: the lead runs `git -C <main checkout> status`, finds who wrote the paths "
    "(each line shows the path, its mtime and the job start), then `orch.py retry`. It clears by itself once the paths are gone. "
    "A dev's job worktree root is its current directory: it writes there, by paths relative to it or absolute under it, never into the main checkout."
)  # the note maincheck.check returns
MAIN_KEPT_STATE = "Nothing was deleted or reverted."  # appended to MAIN_WRITE_NOTE when no tracked change was reverted
MAIN_REVERTED_STATE = (  # appended when the dev's tracked changes were reverted to HEAD after their diff was saved at {patch}
    "The dev's tracked changes were reverted to HEAD; their diff is saved at {patch} (`git -C <main checkout> apply {patch}` restores them)."
)
MAIN_UNSAVED_STATE = (  # appended when the diff could not be saved: nothing is reverted, so the main checkout keeps the writes
    "Tracked changes were NOT reverted: their diff could not be saved to {patch}, so the main checkout keeps them."
)
MAIN_REVERT_KEY = "main_guard_revert"  # pipeline.json key: false keeps the dev's tracked main-checkout writes (named only, nothing reverted)
MAIN_REVERT_DEFAULT = True  # the guard reverts the dev's tracked main-checkout writes after saving them to a patch
MAIN_WRITE_PATCH = (
    "mainwrite-{task}-{stamp}.patch"  # the reverted main-checkout write, kept in the stage's work folder (a retry never overwrites an earlier one)
)
MAIN_DIRT_REWORK_NOTE = (  # dev rework line when the rework file holds a MAIN-DIRT note: the dev writes only in its own worktree
    " MAIN-DIRT was reported for this task: the main checkout changed while it ran. Your job worktree root is your current directory "
    "(shown as `.`); write only there, by paths relative to it or absolute under it, never into the main checkout."
)
BASE_FLAKE_PREFIX = "base-flake:"  # report line for a gate failure that is not the task's (red on the stage base too, or green on an unchanged rerun): `base-flake: <failing test ids>`
FAIL_ID_RES = (  # regexes (group 1 = test id) that read the failing test ids out of a gate's output: pytest summary, unittest header, a plain `FAIL name` line
    r"(?m)^FAILED\s+(\S+)",
    r"(?m)^(?:FAIL|ERROR):\s+(\S+(?: \([\w.]+\))?)",
    r"(?m)^FAIL\s+(\S+)",
)
FAIL_IDS_SHOWN = 10  # failing test ids named in a base-flake line
# the test file a red gate's output names; exactly one of them makes the gate a collision candidate (rerun that file alone)
COLLISION_FILE_RE = r"\btest_\w+\.py\b"
PRE_EXISTING_RED = "pre-existing red: <command>"  # what a dev reports for a gate that is red on the base too
LINK_FIX = (
    "If the task needs that repo, run `{add_repo}` (it gives your job worktree a real worktree of it; edit and commit there) and list the files under `Extra files:`; "
    "a gate that is red on the base is not yours: report `" + PRE_EXISTING_RED + "` and finish DONE."
)
LINK_WRITE_NOTE = (
    "FAIL: path outside this task's worktree: you changed tracked files of a nested repo this task does not list ({files}, +{more} more). "
    "Your change was reverted there and kept as a patch: {patch} (`git apply` it in that repo). Do not edit files outside your worktree. " + LINK_FIX
)
LINK_NEW_FILES_NOTE = (
    "FAIL: path outside this task's worktree: new untracked files appeared in a nested repo this task does not list ({files}, +{more} more). "
    "They were left in place. If you created them, remove them (`rm`) after you have made them in your own worktree. " + LINK_FIX
)
LINK_COMMIT_NOTE = (
    "FAIL: path outside this task's worktree: a commit was made in the main copy of a nested repo this task does not list ({repos}). It was left in place for the lead to revert. "
    "Do not run git commands in a linked repo. " + LINK_FIX
)
LINK_CITE_NOTE = 'FAIL: docs cite out of date: {doc} cites {path}:{line} as "{text}", but that line of {path} no longer has the quoted text. Fix the cite (the line number or the quote) in {doc}.'
LINK_UNSAVED_NOTE = (
    "FAIL: path outside this task's worktree: you changed tracked files of a nested repo this task does not list ({files}, +{more} more). "
    "They were left in place because a patch of them could not be saved. Do not edit files outside your worktree. " + LINK_FIX
)
BASE_WHY_BRANCH = "the main checkout is on `{have}`, not the stage branch `{want}`"
BASE_WHY_DIRTY = "the main checkout has uncommitted changes"
CLEAR_WHY_BAD_RC = "exit {rc} (timeout or cannot run) is never cleared"
CLEAR_WHY_NO_CMD = "the stage base has no such command"
CLEAR_WHY_GREEN = "green on the stage base, so the failure is this task's or a collision with a gate run of another task"
GATE_LABELS = {"gates": "gate", "gates_merge": "gates_merge", "gates_final": "gates_final"}  # plan_check: how a pipeline.json gate list names its entries
EV_FORCED_REMOVE = (
    "forced_remove"  # a worktree removed with --force although its refusal check fired; carries no `task` key (the retry ladder reads every event with one)
)
EV_SETUP = "setup"  # metrics.jsonl: one worktree_setup command of a new job worktree (for = task, cmd, rc, seconds); no `task` key (the retry ladder reads every event with one)
FORCED_WHY_MAX = 300  # chars of the refusal text kept in a `forced_remove` event
EV_MAIN_DIRT_CLEARED = (
    "main_dirt_cleared"  # a BLOCKED run that named main-checkout dirt ran again once the main checkout was clean; `task` and `step` (the BLOCKED run's id)
)
EV_MAIN_RESTORED = "main_restored"  # the main checkout was on another branch at merge time and wt.py switched it back to the stage branch; carries no `task` key (as forced_remove)
PLAN_MAX_PHASES = 99  # highest NN- phase folder number
PLAN_REVIEW_ROUNDS = 1  # plan-reviewer rounds per plan before the plan goes on (one review, one fix pass)
PLAN_REVIEW_SMALL_MAX_TASKS = 7  # a one-phase plan with at most this many tasks gets the light review only
PLAN_EXTRA_TASKS_ALLOWED = 10  # tasks the last-phase cover review may add to the plan (GAP-TASKS above this is refused)
PLAN_REVIEW_MODEL_LIGHT = MODEL_HAIKU  # plan-reviewer model for the phase plan, the task plan and a small one-phase plan
PLAN_REVIEW_MODEL_HEAVY = MODEL_SONNET  # plan-reviewer model for the last-phase cover review and a big one-phase plan
PLAN_REVIEW_REPORT_FILE = "plan-review.md"  # plan-reviewer report, written into the phase folder
PLAN_REVIEW_GAPS_FILE = "plan-review-gaps.md"  # gaps a plan review left after its fix round, written into the phase folder
LOCAL_MAX_TURNS = 25  # localdev agent session turns
EXPLORE_MAX_TURNS = 8  # local-model explore loop (localmodel/agent_files) turns before it must answer; keeps a plan-context build short
LOCAL_MAX_TOKENS = 2500  # localdev model answer cap
LOCAL_GOAL_CHARS = 6000  # localdev: goal text given to the planner prompt
LOCAL_GOAL_SHORT_CHARS = 3000  # localdev: goal text given to the pack prompt
LOCAL_AGENT_TIMEOUT_S = 1500  # one localdev agent session
GATE_TASK_CHARS = 6000  # escalation gate prompt: the whole task brief
GATE_NOTES_CHARS = 8000  # escalation gate prompt: plan-notes.md (tail cut first)
GATE_RULES_CHARS = 8000  # escalation gate prompt: the repo's AGENTS.md (tail cut first)
GATE_NEWEST_CHARS = 8000  # escalation gate prompt: the newest failure report, the live failure (never cut for the others)
GATE_EARLIER_CHARS = 600  # escalation gate prompt: each earlier failure report; a repeated error stays visible, it is never the deciding line
GATE_ANSWER_TOKENS = 3500  # escalation gate: thinking + answer budget; with the char budgets above (about 32k chars, at most one token each) the prompt fits LOCAL_CONTEXT_TOKENS
GATE_PLAN_ERROR = "plan_error"  # judge kind: the brief contradicts a test, an invariant or itself; the lead edits the task
GATE_KINDS = (GATE_PLAN_ERROR, "missing_fact", "owner_choice", "environment")  # judge answer `kind`; any other value is ignored
PLAN_NOTES_FILE, RULES_FILE = "plan-notes.md", "AGENTS.md"  # files the escalation gate shows the judge (stage/feature dir, repo root)
REPORT_GRACE_S = 60  # a session alive this long after its report got a VERDICT line is hung: kill it
POLL_S = 5
SEARCH_TOOL_NAMES = (
    tuple(dict.fromkeys(cmdnames.names_for(_cs_index(), "lp"))) if _cs_index() and "lp" in _cs_index()["commands"] else ("lp", "local.py")
)  # from the command index (name, script file, aliases); names of the local search tool in a Bash command (alias on the container, script on the host); lp_share.py and retro_numbers.py count both
EV_REPORT, EV_DISPATCH, EV_SKIP, EV_SESSION, EV_RETRY = "report", "dispatch", "skip", "session", "retry"  # orch.jsonl / metrics.jsonl event names
GATE_KIND_TASK, GATE_KIND_MERGE, GATE_KIND_FULL = (
    "task",
    "merge",
    "full",
)  # `kind` of a gate_cmd record: the task gates, a wave merge, the final full gate (final_check --stage)
GATE_LEVEL_UNIT, GATE_LEVEL_MERGE, GATE_LEVEL_FULL = (
    "unit",
    "merge",
    "full",
)  # gate time levels: the gate_time_warn_s keys, and the groups of the retro gate stats (gate_stats.py)
GATE_STATS_P90 = 0.9  # fraction of the real runs of a gate level at or under the p90 time of the retro gate stats (nearest rank)
GATE_MODE_FLAG_RE = r"(?<![\w-])--[a-z][a-z-]*(?![\w-])"  # one mode flag of a gate command or script header: a --word token
GATE_MODE_FULL = "full"  # gate mode of a command or script that names no mode flag
HEADER_LINES_MAX = 10  # first lines of a script scanned for the modes its header lists
EV_GATE_CMD = "gate_cmd"  # metrics.jsonl: one record per gate command run by the task gates or a wave merge (gate name, exit code, seconds)
EV_RETRY_REFRESH = "retry_refresh"  # orch.jsonl: `retry` rebased the job branch (or dropped leftovers); no verdict, so history() must not end on it
LEAD_EV = "lead"  # metrics.jsonl event of one lead session of a stage (not a "session" row, so session totals stay the agents' sum)
LEAD_AGENT_EV = "lead-agent"  # metrics.jsonl event of one agent a lead session spawned (subagents/ log) inside the stage window
LEAD_AGENT_UNMATCHED_EV = "lead-agent-unmatched"  # metrics.jsonl event: how many such logs could not be tied to the stage (partly outside its window)
DIGEST_TOTAL_RE = r"\((\d+) sessions, \$([\d.]+)\)"  # header of retro-digest.md written by ops_steps.digest
LIMIT_TEXT_MAX = 300  # a usage-limit notice is a short line; longer text is an agent talking about limits, never a notice
LIMIT_MARGIN_S = 120  # wait this long after the printed reset time before running the session again
LIMIT_FALLBACK_S = 900  # no parsable reset time: wait this long, then try again
LIMIT_RECHECK_S = 900  # a usage-limit wait runs in slices of this many seconds; the session re-reads the clock after each slice (tool setting limit_recheck_s)
LIMIT_WAIT_MAX_S = 8 * 3600  # total waiting per session; beyond this the limit notice stays the result (the run then stops as before)
QRUN_LOG_MAX_AGE_S = int(os.environ.get("QRUN_LOG_MAX_AGE_S", 7 * 24 * 3600))  # qrun-*.log older than this may be pruned by the next qrun run ...
QRUN_LOG_MIN_KEEP = int(os.environ.get("QRUN_LOG_MIN_KEEP", 20))  # ... but only while at least this many newer logs remain (a minimum, no cap)
QRUN_LOG_MAX_COUNT = 100  # at most this many qrun-*.log files stay in the temp dir, fresh or not (a cap, not a floor)
RETRO_ASK_CAUSE_CHARS = 60  # feature_retro: chars of an ask_main message (task id and file paths removed) that name its cause when stops are counted
QRUN_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1"
}  # env every qrun command gets before the project's `test_env` (which may override it): a Python test run leaves no untracked `__pycache__` that the dirty-tree preflight would stop on
QRUN_TMP_PREFIX = "qrun-tmp-"  # each qrun command gets its own TMPDIR with this prefix, removed when the command ends (parallel tasks share /tmp; a project temp-leak guard would blame one task for the other's dirs)
# dir the qrun TMPDIR is created in when it exists and is writable; a long parent TMPDIR would break AF_UNIX socket paths (sockets need short paths)
QRUN_TMP_BASE = os.environ.get("QRUN_TMP_BASE", "/tmp")  # nosec B108 - only the parent of a private mkdtemp dir
QRUN_TMP_MAX_PATH = int(os.environ.get("QRUN_TMP_MAX_PATH", 60))  # bytes: a qrun TMPDIR path longer than this is not used; the tempfile default is used instead
GITHUB_HOSTS = ("github.com",)  # PR-flow hosts (pr_mode.py); `github_hosts` in pipeline.json overrides, [] = check off
GIT_MIN_VERSION = (2, 38)  # `git merge-tree --write-tree` (pipeline/pr_ops.py `_landed`) needs it
PUSH_ERR_HEAD = 200  # chars from the start of a failed push's output kept in the Blocked message (pipeline/pr_ops.py) ...
PUSH_ERR_TAIL = 600  # ... and from its end
SCRIPT_OUTPUT_MAX = 3000  # chars of a script step's output kept in its report (the VERDICT first line is always kept)

STALE_REPORT_NOTE = (
    " First compare the failing report's head (the commit it names, or its timestamp) with the stage branch HEAD now:"
    " when the stage moved on since the report, the report is old, so plan nothing and request a rerun with `{retry}`, then reply DONE, not BLOCKED."
)
STALE_RETRY_STEP = "final-test"  # the step a stale failing report is rerun by
