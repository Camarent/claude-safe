"""Per-project setting defaults (PROJECT_DEFAULTS), the security audit path and word lists and the auditor remit table."""

from .limits import PREREVIEW_SKIP_REVIEWER
from .planning import GENERATED_MARKER_RE

EMPTY_TEST_MARKER_DEFAULT = "# noassert:"  # a test with no assertion carries this marker and a reason on its def line or the line above
EMPTY_TEST_FILE_RE = r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py$"  # test files: test_*.py and *_test.py, matched on the repo path

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
DOCS_WRITES = (
    "docs/",
    "CLAUDE.md",
)  # paths the final-docs writer edits (a trailing `/` = a folder): when the stage diff touches one, final-docs does not start beside the audits
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
GATE_TIME_WARN_DEFAULT = {"unit": 120, "merge": 300, "full": 540}  # seconds per gate kind; the `gate_time_warn_s` default
MERGE_ONLY_TESTS_DEFAULT = ["test_lsp_e2e.py"]  # the `merge_only_tests` default: test files only the merge gate runs
DEV_GATE_FULL_CMDS_DEFAULT = ["scripts/dev.sh cov", "scripts/dev.sh gate"]  # the `dev_gate_full_cmds` default: the dev gate's full commands
DEV_GATE_REPEAT_MAX_DEFAULT = 1  # the `dev_gate_repeat_max` default: a red dev gate run repeats no more than this many times
GATE_SLOTS_DEFAULT = 3  # the `gate_slots` default: gate commands running at once on this host; 0 = no cap
GATE_SLOT_POLL_S = 0.2  # seconds a gate waits between two tries for a free slot (gate_slots.py)
GATE_SLOT_DIR = "cs-gate-slots"  # the folder under the system temp dir that holds the slot files of gate_slots.py
PROJECT_DEFAULTS: dict = {
    "auditor_max_turns": AUDITOR_MAX_TURNS,  # see above
    "auditor_escalate_max_turns": AUDITOR_ESCALATE_MAX_TURNS,  # see above
    "auditor_remit": AUDITOR_REMIT,  # role -> remit topics; see above
    "sec_audit_paths": SEC_AUDIT_PATHS,  # see above; a project list replaces it
    "repo": None,  # repo folder below the workspace; None = autodetect the one that holds AUDIT.md
    "repos": None,  # all repo folders below the workspace for the phase digest (git diff --stat each); None = just `repo`
    "test_command_hint": None,  # the project's real test command, e.g. a wrapper that sets up the environment; a task `Test:` line that runs bare pytest/python -m pytest gets a `testcmd:` error naming it; None = take the first `gates` command that runs pytest behind a wrapper
    "test_ban": [],  # list of {"pattern": regex, "use": text}: a task `Test:` command part matching a pattern is a plan error naming `use`; a malformed entry is ignored
    "scope_guard": "strict",  # off | warn | strict: files a job changed outside the task's Predicted paths (and not named in the task text) go to the reviewer brief; strict = must-address, FAIL unless the task text justifies them; warn = mention only
    "scope_allow": [
        "tests",
        "docs",
        "notes",
    ],  # kinds of outside file that are no finding: tests (path holds a predicted file's name stem), docs (under docs_dir, or .md), notes (.notes/); [] = every outside file counts
    "generated_marker": GENERATED_MARKER_RE,  # regex; a tracked file whose first 5 lines match is a generated file: `plan check` warns when one is in Predicted paths, the scope guard allows it when a predicted source changed
    "audit_file": "AUDIT.md",
    "nested_repo_excludes": [],  # globs (repo-relative) written to every nested git repo's .git/info/exclude and .gitignore by prepare-repo, e.g. ["materials/", "sources/*/pages/"]; [] = none
    "nested_repo_dir_prefix": "",  # prefix (ends with "/") of the dirs that are nested repos a job worktree cannot hold, e.g. "workspace/"; a Predicted path or Test text naming one is a plan problem (only a lead step, `owner: ops`, may name it) and a task naming one without a git repo stops; "" = rule off
    "webtest_tools_available": True,  # false where headless sessions get no browser MCP tools: the final browser check is then recorded as `UNVERIFIED-WEB: skipped: no browser tools` (visible in its report, the retro digest and the PR body) and does not block the stage
    "webtest_requires_tools": ["mcp__chrome-devtools"],  # tool-name prefixes the browser check needs; [] = never skip it
    "webtest_build": None,  # optional build command: the web-tester runs it first on the stage branch checkout and serves only that fresh build, naming the commit in its report; None = brief unchanged
    "docs_dir": "docs",  # folder below the repo that prepare-repo names in a `## Docs` section of CLAUDE.md (written only when the folder exists)
    "docs_impact_gate": True,  # docs impact check (pipeline/docs_impact.py, after the cite check): a changed file or top-level def a doc cites needs that doc changed too; false = off; a dict {"default": bool, <stage>: bool} switches it per stage (docs_impact.gate_on)
    # cite check over the whole docs folder at every stage end (steps_finaltest.py --doc-cites), not only the docs the stage changed; false = no step
    "docs_cite_final": True,
    # docs freshness audit (freshness-auditor, Haiku): once per feature, in its finish phase, beside sec and arch; false = no audit
    "docs_fresh_audit": True,
    "empty_test_gate": True,  # empty-test check (pipeline/empty_tests.py, after the docs impact check): a changed test function with no assert, raises check or mock assertion is FAIL; false = off
    "empty_test_marker": EMPTY_TEST_MARKER_DEFAULT,  # comment on the def line or the line above that skips a test with no assertion; it must give a reason after the colon
    "empty_test_file_re": EMPTY_TEST_FILE_RE,  # regex over the repo path: the changed files the empty-test check reads as test files
    "docs_impact_exempt": [
        "docs/INDEX.md",
        "docs/decisions.md",
        "docs/retro-lessons.md",
    ],  # globs (repo-relative) of docs the docs impact check never flags as stale
    "manual_review_docs": [],  # doc paths (repo-relative) the acceptance reviewer reads with the diff to judge each `eyes:` item; [] = none listed
    "acceptance_prep": [],  # setup commands (npm ci, a venv, a build) the acceptance step runs first in its fresh worktree, before the checklist commands; a failing one fails the step
    "worktree_setup": [],  # commands (venv, npm ci) run with sh -c in each NEW job worktree right after it is created, so a dev starts with a ready environment; a failing one removes the worktree and stops task creation; [] = none
    "worktree_setup_timeout": 900,  # seconds one worktree_setup command may run
    "worktree_requirements": [],  # repo-relative pip requirements globs (e.g. requirements*.txt) installed with `python -m pip install -r` into each NEW job worktree's venv after worktree_setup; a missing file is skipped; [] = none
    "worktree_venv_python": ".venv/bin/python",  # repo-relative python of the job worktree's venv (made by worktree_setup); the worktree_requirements installs run with it; a missing one fails the setup
    "test_roots": [],  # repo-relative test folders (tasks 3, 4 read them); [] = none
    "test_wrap": [],  # test wrapper command parts (tasks 3, 4 read them); [] = none
    "roadmap_file": "",  # repo-relative roadmap file (task 13 reads it); "" = none
    "seed_build": [],  # repo-relative build outputs copied (mtime kept) from the main checkout into each new worktree, so tests work before the first build; [] = none
    "branch_work": "stage/{feature}",  # tasks are merged and tested here; {feature} = feature (or stage) name; a fixed name wins
    "branch_release": None,  # the lead merges work -> release; only release is pushed. None = detected per repo (release_branch); project() fills the main repo's
    "test_env": {},  # env vars the test commands need, e.g. {"SOME_ROOT": "."}
    "pin_manifest": "Cargo.toml",  # manifest file name whose `path =` dependency pins version-bump rewrites to the new version when the target crate uses `version.workspace`; empty = off
    "release_script": None,  # repo-relative script run by version-bump and release: `current [REF]` prints X.Y.Z, `set OLD NEW ENTRY` edits the version files and prints them; None = no release steps
    "quality_tools": None,  # {".ext": ["tool", ...]} for the advisory quality run (audit/qualityrun.py); None = QUALITY_TOOLS. Python/C++ tools run only when listed here, e.g. {".py": ["ruff", "mypy"], ".cpp": ["clang-tidy", "cppcheck"]}
    "prereview_skip": PREREVIEW_SKIP_REVIEWER,  # a skipped local pre-review (diff too big, no diff, server problem): "reviewer" = the paid reviewer reviews, the skip is logged; "ask_main" = the lead decides
    "max_tasks": 30,  # most tasks one stage may plan that have not started; more work goes into phases (Plan.md) or a second stage. Tasks that have started and fix tasks of a fix round do not count
    "gate_flake_rerun": True,  # a task gate run that is red reruns its red commands once, unchanged, in the job worktree; a command green on the rerun is a flake, not the task's failure (the report names it `base-flake: <test ids>`); False = every red counts
    "gate_collision_reruns": 1,  # a red task gate whose output names one test file reruns that file alone this many times; green on a rerun is a collision, not the task's failure
    "gate_base_check": True,  # a task gate run that is red also runs its red commands on the stage base (the main checkout); a command red there too is not counted against the task (the report names it); False = every red counts
    "gate_watch": [],  # folders below the main checkout (e.g. ["workspace/progress"]) a task gate must not change: the gate report warns, naming the changed paths and the tests that mention them; [] = off
    "preflight_gates": True,  # plan.py run / wave.py run / wave.py feature run the gates on the base first and refuse a red base; false = no check
    "serve_commands": [],  # extra command prefixes servectl may start (the project's own dev server), e.g. ["npm run serve"]; the loopback static server is always allowed (SERVE_DEFAULT_COMMANDS). A prefix ending in <port> takes exactly one port word
    "publish": {},  # {artifact_url: {"cwd": dir below the project, "build": one command, "page": file below cwd, "files": {published_path: file below cwd}}}: the republish step rebuilds the page and publishes it in place; see publish_entries
    "missing_binaries": [
        "ffmpeg",
        "ffprobe",
    ],  # binaries a red-base failure names when its output has MISSING_BINARY_MARK (the red-base message adds a line listing them)
    "tool_settings": {},  # {key: value} overriding a named tool setting (configuration/literals.py `tool_setting`); env CS_<NAME> wins over it
    "acceptance_timing_flag": None,  # flag appended to the project test command in the acceptance command lines (timing output goes to the stage reports folder); None = no flag
    "pipeline_install_check": None,  # command the driver runs after each phase merge, before the next phase; a failure blocks the stage; None = no check
    "ci_available": True,  # false where no CI runs for the repo: pr-ready and master-merged skip the CI waits and merge on green local gates, and say so
    "wiring_dirs": [],  # repo folders whose new source files must be imported by another tracked file (orphan check, plan_wiring_orphans.py); [] = no check
    "gate_time_warn_s": GATE_TIME_WARN_DEFAULT,  # seconds per gate kind {"unit", "merge", "full"} above which a gate run is slow
    "merge_only_tests": MERGE_ONLY_TESTS_DEFAULT,  # test files run only by the merge gate (gates_merge), never by a task's Test line or the unit gates
    "dev_gate_full_cmds": DEV_GATE_FULL_CMDS_DEFAULT,  # commands the dev gate runs in full (dev_gate_audit); [] = none
    "dev_gate_repeat_max": DEV_GATE_REPEAT_MAX_DEFAULT,  # most times the dev gate repeats a red run; 1 = no repeat
    "gate_slots": GATE_SLOTS_DEFAULT,  # gate commands that may run at once across every driver on this host (gate_slots.py); 0 = no cap. Caps the load so a step's gate runs fit the 540 s budget
}
GATE_FLAKE_RERUN_KEY = "gate_flake_rerun"  # the PROJECT_DEFAULTS key above
GATE_BASE_CHECK_KEY = "gate_base_check"  # the PROJECT_DEFAULTS key above
GATE_COLLISION_RERUNS_KEY = "gate_collision_reruns"  # the PROJECT_DEFAULTS key above
GATE_TIME_WARN_KEY = "gate_time_warn_s"  # the PROJECT_DEFAULTS key above: seconds per gate kind before a gate run is noted as slow
MERGE_ONLY_TESTS_KEY = "merge_only_tests"  # the PROJECT_DEFAULTS key above: test files only the merge gate runs
DEV_GATE_FULL_CMDS_KEY = "dev_gate_full_cmds"  # the PROJECT_DEFAULTS key above: the dev gate's full commands
DEV_GATE_REPEAT_MAX_KEY = "dev_gate_repeat_max"  # the PROJECT_DEFAULTS key above: most repeats of a red dev gate run
GATE_SLOTS_KEY = "gate_slots"  # the PROJECT_DEFAULTS key above: gate commands running at once on this host
PREFLIGHT_KEY = "preflight_gates"  # the PROJECT_DEFAULTS key above
EMPTY_TEST_KEY = "empty_test_gate"  # the PROJECT_DEFAULTS key (empty_tests.py gate switch)
EMPTY_TEST_DEFAULT = True  # the key when pipeline.json does not set it
EMPTY_TEST_MARKER_KEY = "empty_test_marker"  # the PROJECT_DEFAULTS key
EMPTY_TEST_FILE_KEY = "empty_test_file_re"  # the PROJECT_DEFAULTS key
SECRET_SCAN_CONFIG_KEY = "secret_scan_config"  # nosec B105 - a JSON key name, not a secret; pipeline.json key, not a PROJECT_DEFAULTS key: a gitleaks config path (relative to the repo); unset = gitleaks' own rules
GITLEAKS_TOOL = "gitleaks"  # the secret scanner binary (gate_secrets.scan_diff)
GITLEAKS_CLEAN_RC = 0  # gitleaks exit code: no leak found
GITLEAKS_LEAKS_RC = 1  # gitleaks exit code: at least one leak found
SECRET_SCAN_TIMEOUT_S = 120  # seconds for one gitleaks run over the task diff
WIRING_DIRS_KEY = "wiring_dirs"  # the PROJECT_DEFAULTS key: repo folders the orphan check covers
WIRING_SOURCE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")  # source files the orphan check reads and checks
WIRING_PY_EXT = ".py"  # the Python source extension: its import lines name dotted modules, not quoted paths
WIRING_ALIAS_PREFIXES = ("@/",)  # JS/TS path-alias prefixes of an import specifier: the rest is a repo path
WIRING_TEST_RE = r"(^|/)(test_[^/]*|[^/]*\.(test|spec)\.[^/]+)$|(^|/)tests?/"  # regex: a test file, never an orphan
WIRING_IMPORT_LINE_RE = r"^\s*(?:\}\s*)?(?:from\s|import\b|export\b.*\bfrom\b|(?:const|let|var)\s.*\brequire\s*\(|require\s*\(|(?:await\s+)?import\s*\()"  # regex: a line that starts an import (a comment or a string never does)
WIRING_QUOTED_RE = r"""['"]([^'"]+)['"]"""  # regex: the module specifier of a JS/TS import line
WIRING_PY_FROM_RE = r"^\s*from\s+([\w.]+)\s+import\s+(.+)$"  # regex: a Python `from X import Y` line
WIRING_PY_IMPORT_RE = r"^\s*import\s+([\w.]+)"  # regex: a Python `import X` line
WIRING_SPLIT_RE = r"[^\w-]+"  # regex: splits a module specifier into the names it is made of
KNIP_CONFIG_FILE = "knip.json"  # the knip config at the repo root; its `entry` globs are never orphans
KNIP_ENTRY_KEY = "entry"  # the knip.json key that lists the entry files
WIRING_ORPHAN_LINE = "- orphan: {}"  # one line of the orphan report per orphan file
WIRING_NONE_TEXT = "No orphans: every new file under wiring_dirs has an importer.\n"  # the report text when no orphan is found
