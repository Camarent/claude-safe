"""Environment variable names and their defaults, artifact and add-dir agent lists, git safe-directory env."""

from .limits import GIT_SAFE_DIR
from .paths import AGENTS_DIR

# environment variables (names) and their defaults
ENV_CLAUDE_FLAGS = "WAVE_CLAUDE_FLAGS"  # replaces DEFAULT_FLAGS for headless sessions
HOST_AGENT_PREFIX = "pl-"  # scripts/host_agents.py installs the container agents on the host as `<prefix><name>` (its --prefix default)
ENV_REVIEW_DIR = "PIPELINE_REVIEW_DIR"  # set for the reviewer session only: the one directory it may edit (tools/review_guard.py)
ENV_SCRATCH_DIR = "PIPELINE_SCRATCH_DIR"  # set for every agent session: the stage scratch dir WORK_DIR/<stage>/SCRATCH_DIR (also TMPDIR unless pipeline.json test_env sets it); review_guard lets the reviewer write there
ENV_AGENT_KIND = "PIPELINE_AGENT_KIND"  # set for every agent session: its dispatch agent name (denial_log.py records it per denial)
ENV_DENIAL_SESSION = (
    "PIPELINE_DENIAL_SESSION"  # set for every agent session: a token unique to it (denial_log.py stamps it on each row; claude.session reads only its own rows)
)
ENV_GUARD_WORKTREE = "PIPELINE_GUARD_WORKTREE"  # set for a dev session: its job worktree root, the one tree its writes may go to (tools/write_guard.py)
ENV_GUARD_MAIN = "PIPELINE_GUARD_MAIN"  # set for a dev session: the main checkout root, which write_guard denies (unless under ENV_GUARD_ALLOW)
ENV_GUARD_ALLOW = "PIPELINE_GUARD_ALLOW"  # os.pathsep list: extra dirs under the main checkout that write_guard allows
ENV_GUARD_JOB = "PIPELINE_GUARD_JOB"  # the job name (stage/task) write_guard puts in each log line, so MAIN-DIRT can name the writer
ENV_GUARD_LOG = "PIPELINE_GUARD_LOG"  # JSONL file write_guard appends {ts,job,tool,path} to for each denied main-checkout write
GUARD_LOG_FILE = "write-guard.jsonl"  # default name of the guard log inside the job scratch dir
GUARD_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")  # tool names write_guard checks (file-writing tools of the dev session)
WORKTREE_CWD_AGENTS = ("dev", "pl-dev")  # container and host names of the roles whose session cwd is the job worktree (only dev changes)
NO_REVIEW_ROOT = "/nonexistent/review-root"  # ENV_REVIEW_DIR value when the reviewer has no job worktree: every edit is denied
ENV_TIMEOUT = "WAVE_TIMEOUT"  # seconds per headless session
ENV_SCRIPT_TIMEOUT = "ORCH_SCRIPT_TIMEOUT"  # seconds per no-model script step (SCRIPT_TIMEOUT_S default)
ENV_NO_INDEX = "ORCH_NO_INDEX"  # set: skip the post-merge index refresh
ENV_QRUN_NO_RECORD = "QRUN_NO_RECORD"  # set: qrun writes no gate-run record (pipeline/gate_record.py)
ENV_GATED = "ORCH_GATED"  # comma list of agents the lead may only start after approval
ENV_GATE_KIND = "PIPELINE_GATE_KIND"  # set for the reviewer session only: GATE_KIND_MUTATION; qrun then uses MUTATION_GATE_TIMEOUT_S
GATE_KIND_MUTATION = "mutation"  # ENV_GATE_KIND value of a reviewer mutation check
GATE_KIND_FLAKE = "flake"  # gate-run record kind of a run that failed then passed on a retry: never a pass (gate_record.find_passed)
ENV_AGENTS_DIR = "ORCH_AGENTS_DIR"
ENV_MAX_DISPATCH = "ORCH_MAX_DISPATCH"
ENV_MAX_OPUS = "ORCH_MAX_OPUS"
ENV_NO_CONTEXT = "PLAN_NO_CONTEXT"  # set (tests): the planner writes no plan-context.md
ENV_OVERRIDES = "PIPELINE_OVERRIDES"  # a JSON object overlaid last on project(): per-run settings, after pipeline.json and LOCAL_CONFIG_FILE
LOCAL_CONFIG_FILE = "pipeline.local.json"  # git-ignored <workspace> overrides, overlaid on pipeline.json (project.py); never tracked
ENV_LEAD_LOG = "PIPELINE_LEAD_LOG"  # lead transcript file(s) or folder(s), os.pathsep separated: the feature retro adds the lead's cost lines (pipeline.json `lead_log` is the alternative)
ENV_NO_LEADS = "PIPELINE_NO_LEADS"  # set (tests): skip the mandatory first code search
ENV_ARTIFACT = "CLAUDE_CODE_ARTIFACT"  # headless sessions get the Artifact tool only with this set; set for ARTIFACT_AGENTS only
ARTIFACT_ON = "1"  # ENV_ARTIFACT value
ARTIFACT_AGENTS = ("publisher", "pl-publisher")  # container name, host name: the only sessions that publish artifacts
ARTIFACT_ALLOW_FLAGS = (
    "--allowedTools",
    "Artifact",
)  # headless publisher only: the Artifact tool is allowed without a prompt (its "replacing" approval cannot be answered headless); inserted before the prompt's `--`
ADD_DIR_AGENTS = (
    "architect",
    "pl-architect",
    "arch-auditor",
    "pl-arch-auditor",
    "sec-auditor",
    "pl-sec-auditor",
    "freshness-auditor",
    "pl-freshness-auditor",
    "scout",
    "pl-scout",
    "researcher",
    "pl-researcher",
)  # container and host names of the read-only roles that get ADD_DIR_FLAG; never dev, reviewer, merger, ops, publisher, web-tester, ux-reviewer, docs-writer
SCRATCH_ADD_AGENTS = (
    "dev",
    "pl-dev",
    "reviewer",
    "pl-reviewer",
)  # container and host names of the roles that get ADD_DIR_FLAG for SCRATCH_ADD_DIR only (owner decision 2026-11-17: `ls /tmp` style reads were denied 309 times in 5 days); they never get the project root
SCRATCH_ADD_DIR = "/tmp"  # nosec B108  # the one extra dir SCRATCH_ADD_AGENTS may read (a read permission for the container scratch space, not a file the pipeline creates)
ADD_DIR_FLAG = "--add-dir"  # lets the session cd and read below the project root, outside its cwd; inserted before the prompt's `--`
ENV_PROJECT_ROOT = "PIPELINE_PROJECT_ROOT"  # project dir the driver was started with; default DEFAULT_WORKSPACE
ADD_DIRS_KEY = "add_dirs"  # pipeline.json key: list of dirs replacing the project root for ADD_DIR_AGENTS
DEFAULT_GATED = "publisher"
DEFAULT_AGENTS_DIR = AGENTS_DIR
DEFAULT_MAX_OPUS = 3
GIT_SAFE_ENV = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": GIT_SAFE_DIR}  # repos may be owned by another uid

DEFAULT_WORKSPACE = "/workspace"  # the container mount of the project workspace: the one reference value for tools that get no --dir

QUALITY_TOOLS = {".rs": ("clippy",), **dict.fromkeys((".js", ".mjs"), ("eslint", "knip", "jscpd", "madge"))}  # default extension -> tools of the quality run
