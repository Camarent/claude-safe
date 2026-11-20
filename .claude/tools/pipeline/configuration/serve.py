"""Named settings of `servectl` (tools/servectl.py): the TTL-bound dev server wrapper the web-tester uses instead of kill/pkill."""

from .paths import cs_names

SERVE_TTL_S = 900.0  # default lifetime of a server started by servectl: it ends itself after this many seconds (env SERVECTL_TTL or `--ttl` override)
SERVE_TTL_ENV = "SERVECTL_TTL"  # env var that overrides SERVE_TTL_S
SERVE_DIR_ENV = "SERVECTL_DIR"  # env var that names the state dir (pid files, logs); `--state-dir` overrides it
SERVE_DIR_NAME = "servectl"  # state dir under the cwd's WORK_DIR when neither flag nor env names one
SERVE_STATE_SUFFIX = ".json"  # one state file per started server, named <id>.json in the state dir
SERVE_STOP_GRACE_S = 5.0  # seconds between SIGTERM and SIGKILL of a server group
SERVE_PORT_WAIT_S = 30.0  # default bound of `start --port-wait`
SERVE_POLL_S = 0.1  # poll step of the port wait and the stop wait
SERVE_SUBCOMMANDS = ("start", "stop", "list")  # the only servectl subcommands an agent may run (cmd_guard denies the rest)
SERVE_SCRIPT = "servectl.py"  # the tool file; the cs-* wrapper is found from paths.json
SERVE_WRAPPER = next((n for n in cs_names() if n.endswith("servectl")), "cs-servectl")  # the wrapper name of servectl.py
SERVE_HOST = "127.0.0.1"  # the only address a UI check may bind; a bind to any other address is denied (cmd_guard.py)
SERVE_PORT_TOKEN = "<port>"  # nosec B105  # a command placeholder, not a credential; last word of a command prefix: exactly one all-digit word (the port) may follow it
SERVE_DIR_FLAG = "--directory"  # the http.server option that names the folder to serve; servectl accepts it only for a folder inside the workspace (serve_rule.start_refusal)
SERVE_DIR_TOKEN = "<dir>"  # nosec B105  # a command placeholder: one word, a directory path without `..` and not an option
SERVE_DEFAULT_COMMANDS = (  # command prefixes servectl may start without any project setting: the loopback static server only, with or without its directory
    f"python3 -m http.server --bind {SERVE_HOST} {SERVE_PORT_TOKEN}",
    f"python3 -m http.server --bind {SERVE_HOST} {SERVE_PORT_TOKEN} {SERVE_DIR_FLAG} {SERVE_DIR_TOKEN}",
)  # pipeline.json `serve_commands` adds the project's own server commands (serve_rule.py checks them)
SERVE_SHELLS = ("sh", "bash", "dash", "zsh", "ksh")  # a shell word followed by a `-c`-style option is refused: no `sh -c` / `bash -c` start
SERVE_SHELL_META = (";", "&", "|", "$(", "`", ">", "<", "\n")  # refused in any word of the inner command: no shell operators, no substitution
