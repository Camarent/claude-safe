"""Headless `claude -p` session flags: the Bash allowlist, serve allowlist, UI paths and test runner patterns."""

import os

from .paths import QRUN_PATTERN, TOOLS_DIR, cs_names
from .serve import SERVE_HOST, SERVE_SUBCOMMANDS, SERVE_WRAPPER

GUARD_WRAPPERS = frozenset(
    {"cs-cmd-guard", "cs-review-guard", "cs-model-guard"}
)  # hook wrappers settings.json runs; agents never run them, so BASH_ALLOW leaves them out
SNIP_WRAPPER = "snip"  # output-trimming wrapper; `snip run <cmd>` is judged as <cmd> by review_guard.py
SCRATCH_ENV = "TMPDIR"  # env var naming the agent scratch dir: mkdir/mktemp may write under it (review_guard.py)


# headless `claude -p` sessions
PLAIN_SHELL_TOOLS = (
    "mkdir",
    "cat",
    "head",
    "tail",
    "wc",
    "sort",
    "uniq",
    "cut",
    "echo",
    "ls",
    "jq",
    "grep",
)  # plain tools every headless role may run (owner decision: fine inside the container); read-only except mkdir; no `find` (-exec/-delete cannot be blocked for non-reviewer roles), no rm/sed/awk/tee/xargs
QRUN_CHAIN_READ_ONLY: tuple[str, ...] = (
    "ls",
    "echo",
    "wc",
    "cat",
    "head",
    "tail",
    "test",
)  # commands that may follow `qrun ... &&` (cmd_guard): read-only, no redirect check
DENIED_CMD_MAX = 600  # chars of each denied command kept in metrics.jsonl `denied`
PY_INLINE = "python3 -I -c"  # inline python; owner accepts that `python -c` can write anywhere
PY_STDIN = "python3 -I -"  # python read from stdin (a heredoc body)
SED_PRINT_ALLOW = (
    "sed -n *"  # `sed -n 'A,Bp' file` only: cmd_guard denies every other sed form (-i, -e, -f, w/e/r commands) before it runs; owner decision 2026-11-17
)
SED_PRINT_SCRIPT = r"\d+(?:,(?:\d+|\$))?p"  # the one sed script allowed: print one line or a line range
BASH_ALLOW = (  # `Bash(<pattern>)` entries of the headless allowlist; a compound command needs every part allowed; pipeline.json `bash_allow` replaces it
    f"python3 -I {TOOLS_DIR}/*",
    f"{PY_INLINE} *",
    PY_STDIN,
    f"{PY_STDIN} *",
    *(f"{t} *" for t in PLAIN_SHELL_TOOLS),
    "lp *",
    SED_PRINT_ALLOW,
    "mktemp *",
    "export *=*",
    "cd *",
    "node *",
    "scripts/*",
    "bash scripts/*",
    "cargo *",
    "git *",
    "cmake *",
    "ctest *",
    "make *",
    "snip *",
    "/usr/local/bin/snip *",  # snip = the output-trimming wrapper agents put before commands
    '"snip" *',  # harmless: a live `claude -p` test (2026-11-09) shows a quoted head already matches the unquoted pattern, so these two entries fix nothing
    '"/usr/local/bin/snip" *',
    *(
        f"{n} *" for n in cs_names() if n not in GUARD_WRAPPERS and n != SERVE_WRAPPER
    ),  # every cs-* wrapper of paths.json except the guard hooks; a new tool needs no edit here
    *(
        f"{SERVE_WRAPPER} {s}{arg}" for s in SERVE_SUBCOMMANDS for arg in ("", " *")
    ),  # servectl: only its start/stop/list subcommands (the server wrapper, see SERVE_SUBCOMMANDS)
)
SERVE_ALLOW = (  # localhost-only static serve for web-tester / ux-reviewer; pipeline.json `serve_allow` replaces it (e.g. the project's own serve command)
    f"python3 -m http.server --bind {SERVE_HOST} *",
    f"python3 -I -m http.server --bind {SERVE_HOST} *",
)
UI_PATHS = (
    "*.html",
    "*.css",
    "*.js",
    "*.jsx",
    "*.ts",
    "*.tsx",
    "*.vue",
    "*.svelte",
    "*.wasm",
)  # changed files that make a UI check mandatory; pipeline.json `ui_paths` replaces it
OTHER_ALLOW = "ToolSearch mcp__chrome-devtools"  # allowed non-Bash tools
BASE_FLAGS = "--permission-mode acceptEdits --allowedTools Read Edit Write Grep Glob"
PROJECT_FILE = "pipeline.json"  # per-project settings file name
TEST_RUNNERS = (  # cmd_guard.py: regexes of command heads that count as a test run; pipeline.json `test_runners` replaces it
    QRUN_PATTERN,
    r"(?:\S*/)?node\s+--test",
    r"(?:\S*/)?pytest",
    r"python3?\s+(?:-\w+\s+)*-m\s+(?:pytest|unittest)",
    r"python3?\s+(?:-\w+\s+)*\S*ptest\.py",
    r"(?:\S*/)?cargo\s+test",
    r"(?:\S*/)?npm\s+(?:run\s+)?test",
    r"(?:\S*/)?ctest",
    r"(?:\S*/)?make\s+(?:\S+\s+)*?(?:test|check)",
    r"(?:bash\s+|\./)?(?:\S*/)?scripts/\S+\s+(?:test|check)",
)


def claude_flags(cfg=None):
    """-> the headless `claude -p` flag string: BASE_FLAGS, the Bash allowlist (cfg `bash_allow` or BASH_ALLOW), OTHER_ALLOW."""
    allow = tuple((cfg or {}).get("bash_allow") or BASH_ALLOW) + serve_allow(cfg)
    return f"{BASE_FLAGS} " + " ".join(f"'Bash({a})'" for a in allow) + f" {OTHER_ALLOW}"


def scratch_allow(scratch, cfg=None):
    """-> `Bash` patterns that let an agent run python script files from its own SCRATCH dir (`python3 -I <scratch>/x.py args`); () without a scratch dir or when cfg `bash_allow` replaces the allowlist. unzip/tar -x into scratch are not expressible safely (a `*` argument could add a second -d/-C), so they stay denied."""
    if not scratch or (cfg or {}).get("bash_allow"):
        return ()
    return (f"python3 -I {scratch.rstrip('/')}/*",)


def serve_allow(cfg=None):
    """-> `Bash` patterns of the localhost serve: cfg `serve_allow` or SERVE_ALLOW."""
    return tuple((cfg or {}).get("serve_allow") or SERVE_ALLOW)


def ui_paths(cfg=None):
    """-> globs of the files whose change makes the final UI check mandatory: cfg `ui_paths` or UI_PATHS."""
    return tuple((cfg or {}).get("ui_paths") or UI_PATHS)


UX_FLOWS_KEY = "ux_flows"  # pipeline.json key: path (relative to the repo root) of the project's `steps.json` of named UI flows (ux_shots.js format plus optional `covers` globs)
WEBTEST_BATCH = 4  # UI tasks one web-tester step checks (each with all its done criteria); more UI tasks get more steps
WEBTEST_MODEL_KEY = "webtest_model"  # pipeline.json key that replaces WEBTEST_MODEL
WEBTEST_EFFORT = "medium"  # reasoning effort of the final browser check
WEBTEST_WIDTHS = "phone 390px and desktop 1280px"  # widths the browser check names in its brief
WEBTEST_STEP = "final-webtest"  # id of the first browser-check batch; later batches add WEBTEST_BATCH_SUFFIX, fix-task batches WEBTEST_FIX_SUFFIX, a re-check WEBTEST_AGAIN_SUFFIX
WEBTEST_BATCH_SUFFIX, WEBTEST_FIX_SUFFIX, WEBTEST_AGAIN_SUFFIX = "-b", "-f", "-2"
FIX_WEB_STEP = "fix-plan-web"  # the fix round after a failed browser check
WEBTEST_MODEL = "sonnet"  # model of the final browser check: multi-step flows are too much for the cheapest model (pipeline.json `webtest_model` replaces it)
UX_FLOW_ACTIONS = ("click", "type", "press", "wait", "eval")  # action keys of one flow step (ux_shots.js runAction)


def ux_flows(cfg=None):
    """-> the `ux_flows` path of cfg (repo-relative), '' when none is configured."""
    return str((cfg or {}).get(UX_FLOWS_KEY) or "")


def webtest_model(cfg=None):
    """-> model of the final browser check: cfg `webtest_model` or WEBTEST_MODEL."""
    return str((cfg or {}).get(WEBTEST_MODEL_KEY) or WEBTEST_MODEL)


def test_runners(cfg=None):
    """-> regex strings for test-run command heads: cfg `test_runners` or TEST_RUNNERS."""
    return tuple((cfg or {}).get("test_runners") or TEST_RUNNERS)


DEFAULT_FLAGS = claude_flags()


def ops_flags(d=None):
    """ops only: the rm pattern for cleanup under <run dir>/.work/<stage>/ (default: the process cwd)."""
    return [f"Bash(rm {d or os.getcwd()}/.work/*)"]


OPS_FLAGS = ops_flags()
