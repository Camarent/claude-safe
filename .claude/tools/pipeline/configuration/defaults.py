"""Per-project setting defaults (PROJECT_DEFAULTS), the security audit path and word lists and the auditor remit table."""

from .limits import PREREVIEW_SKIP_REVIEWER

# per-project settings: <workspace>/pipeline.json overrides these defaults
# Opus sec audit runs only when the diff touches one of these (fnmatch on the repo path, or an added line matching SEC_AUDIT_CONTENT); `sec_audit_paths: ["*"]` = always.
SEC_AUDIT_PATHS = [
    "*auth*", "*login*", "*session*", "*secret*", "*token*", "*cred*", "*password*", "*crypt*", "*.env*", "*.pem",
    "*subprocess*", "*shell*", "*http*", "*server*", "*socket*", "*guard*", "*settings*", "*.json", "*.lock", ".git*", "*/.git*",
    "*.js", "*.ts", "Dockerfile*", "*/dockerfile*", "*.toml", "*hooks*", "*permission*", "*sandbox*",
    ".github/*", ".gitlab-ci*", "*compose*.y*ml", "*entrypoint*", "*install*.sh", "*deploy*", "*release*.sh",
]  # fmt: skip  # matched case-insensitively (path lowercased) with fnmatch
SEC_AUDIT_WORDS = (
    "net",
    "url",
    "path",
    "cmd",
    "exec",
)  # default list only: a word of the file's basename (split at non-alphanumerics), so `network.py` and `pathlib_x.py` do not hit but `exec_cmd.py` does
SEC_CODE_EXTS = (
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".kt",
    ".swift",
)  # the content scan reads these only (not docs)
SEC_AUDIT_CONTENT = r"\b(subprocess|os\.system|Popen|exec[lv]?p?e?\(|eval\(|child_process|Command::new|shell=True|password|secret|api[_-]?key|token|https?://|socket|bind\(|chmod|os\.environ|getenv|innerHTML|dangerouslySetInnerHTML|pickle\.loads|yaml\.load|unsafe\s*\{|fetch\(|requests\.post|rmtree)"

# auditor remit: the topics (lowercase substrings) an auditor reviews from the Plan.md `## Acceptance checklist`; an item with none of them is not reviewed by that auditor.
# A role missing here (ux-reviewer) gets the whole checklist. pipeline.json `auditor_remit` replaces this table.
AUDITOR_REMIT = {
    "sec-auditor": ("secret", "dependenc", "private", "injection", "history"),
    "arch-auditor": ("architect", "code health", "duplicat", "dead code", "module boundar"),
}
AUDITOR_MAX_TURNS = 40  # `claude --max-turns` for an auditor session (sec-auditor ran 64 turns, 116 tool calls, 57% of a phase's cost); pipeline.json `auditor_max_turns`, 0 = no cap
AUDITOR_ESCALATE_MAX_TURNS = 80  # `--max-turns` of the Opus rerun after an auditor ESCALATE; pipeline.json `auditor_escalate_max_turns`, 0 = no cap
REMIT_MATCH_CHARS = 30  # a report line names a checklist item when it holds the first this-many characters of the item text (case-insensitive)
PROJECT_DEFAULTS: dict = {
    "auditor_max_turns": AUDITOR_MAX_TURNS,  # see above
    "auditor_escalate_max_turns": AUDITOR_ESCALATE_MAX_TURNS,  # see above
    "auditor_remit": AUDITOR_REMIT,  # role -> remit topics; see above
    "sec_audit_paths": SEC_AUDIT_PATHS,  # see above; a project list replaces it
    "repo": None,  # repo folder below the workspace; None = autodetect the one that holds AUDIT.md
    "repos": None,  # all repo folders below the workspace for the phase digest (git diff --stat each); None = just `repo`
    "test_command_hint": None,  # the project's real test command, e.g. a wrapper that sets up the environment; a task `Test:` line that runs bare pytest/python -m pytest gets a `testcmd:` warning naming it; None = take the first `gates` command that runs pytest behind a wrapper
    "scope_guard": "strict",  # off | warn | strict: files a job changed outside the task's Predicted paths (and not named in the task text) go to the reviewer brief; strict = must-address, FAIL unless the task text justifies them; warn = mention only
    "scope_allow": ["tests", "docs", "notes"],  # kinds of outside file that are no finding: tests (path holds a predicted file's name stem), docs (under docs_dir, or .md), notes (.notes/); [] = every outside file counts
    "audit_file": "AUDIT.md",
    "nested_repo_excludes": [],  # globs (repo-relative) written to every nested git repo's .git/info/exclude and .gitignore by prepare-repo, e.g. ["materials/", "sources/*/pages/"]; [] = none
    "nested_repo_dir_prefix": "",  # prefix (ends with "/") of the dirs that are nested repos a job worktree cannot hold, e.g. "workspace/"; a Predicted path or Test text naming one is a plan problem (only a lead step, `owner: ops`, may name it) and a task naming one without a git repo stops; "" = rule off
    "webtest_build": None,  # optional build command: the web-tester runs it first on the stage branch checkout and serves only that fresh build, naming the commit in its report; None = brief unchanged
    "docs_dir": "docs",  # folder below the repo that prepare-repo names in a `## Docs` section of CLAUDE.md (written only when the folder exists)
    "acceptance_prep": [],  # setup commands (npm ci, a venv, a build) the acceptance step runs first in its fresh worktree, before the checklist commands; a failing one fails the step
    "worktree_setup": [],  # commands (venv, npm ci) run with sh -c in each NEW job worktree right after it is created, so a dev starts with a ready environment; a failing one removes the worktree and stops task creation; [] = none
    "worktree_setup_timeout": 900,  # seconds one worktree_setup command may run
    "seed_build": [],  # repo-relative build outputs copied (mtime kept) from the main checkout into each new worktree, so tests work before the first build; [] = none
    "branch_work": "stage/{feature}",  # tasks are merged and tested here; {feature} = feature (or stage) name; a fixed name wins
    "branch_release": None,  # the lead merges work -> release; only release is pushed. None = detected per repo (release_branch); project() fills the main repo's
    "test_env": {},  # env vars the test commands need, e.g. {"SOME_ROOT": "."}
    "review_ccn": 10,  # a task whose diff changes a function with cyclomatic complexity above this gets the Sonnet reviewer (lizard; any error -> Haiku)
    "pin_manifest": "Cargo.toml",  # manifest file name whose `path =` dependency pins version-bump rewrites to the new version when the target crate uses `version.workspace`; empty = off
    "release_script": None,  # repo-relative script run by version-bump and release: `current [REF]` prints X.Y.Z, `set OLD NEW ENTRY` edits the version files and prints them; None = no release steps
    "quality_tools": None,  # {".ext": ["tool", ...]} for the advisory quality run (audit/qualityrun.py); None = QUALITY_TOOLS. Python/C++ tools run only when listed here, e.g. {".py": ["ruff", "mypy"], ".cpp": ["clang-tidy", "cppcheck"]}
    "prereview_skip": PREREVIEW_SKIP_REVIEWER,  # a skipped local pre-review (diff too big, no diff, server problem): "reviewer" = the paid reviewer reviews, the skip is logged; "ask_main" = the lead decides
    "max_tasks": 30,  # most tasks one stage may plan that have not started; more work goes into phases (Plan.md) or a second stage. Tasks that have started and fix tasks of a fix round do not count
    "gate_flake_rerun": True,  # a task gate run that is red reruns its red commands once, unchanged, in the job worktree; a command green on the rerun is a flake, not the task's failure (the report names it `base-flake: <test ids>`); False = every red counts
    "gate_base_check": True,  # a task gate run that is red also runs its red commands on the stage base (the main checkout); a command red there too is not counted against the task (the report names it); False = every red counts
    "gate_watch": [],  # folders below the main checkout (e.g. ["workspace/progress"]) a task gate must not change: the gate report warns, naming the changed paths and the tests that mention them; [] = off
    "preflight_gates": True,  # plan.py run / wave.py run / wave.py feature run the gates on the base first and refuse a red base; false = no check
    "serve_commands": [],  # extra command prefixes servectl may start (the project's own dev server), e.g. ["npm run serve"]; the loopback static server is always allowed (SERVE_DEFAULT_COMMANDS). A prefix ending in <port> takes exactly one port word
    "publish": {},  # {artifact_url: {"cwd": dir below the project, "build": one command, "page": file below cwd, "files": {published_path: file below cwd}}}: the republish step rebuilds the page and publishes it in place; see publish_entries
    "missing_binaries": [
        "ffmpeg",
        "ffprobe",
    ],  # binaries a red-base failure names when its output has MISSING_BINARY_MARK (the red-base message adds a line listing them)
}
GATE_FLAKE_RERUN_KEY = "gate_flake_rerun"  # the PROJECT_DEFAULTS key above
GATE_BASE_CHECK_KEY = "gate_base_check"  # the PROJECT_DEFAULTS key above
PREFLIGHT_KEY = "preflight_gates"  # the PROJECT_DEFAULTS key above
