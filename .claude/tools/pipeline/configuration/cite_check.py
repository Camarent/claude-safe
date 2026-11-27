"""Names of the cite check: which added doc and comment lines are read, how a `path:line` cite is found and how far a named symbol may sit from it."""

import re

CITE_WINDOW_LINES = 3  # lines either side of a cited line where the symbol named just before the cite must occur
CITE_SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", ".wt", ".work", "__pycache__"}
)  # folders not searched when a bare file name in a cite is resolved to its one tracked file
CITE_REPORT_NAME = "report"  # label of the dev report text in messages (it is not a file of the worktree)
CITE_MD_EXTENSIONS = (".md",)  # added lines of these files are read whole, outside fenced blocks
CITE_COMMENT_MARKS = {  # code files: the comment marker; only the tail after it is read
    ".py": "#",
    ".sh": "#",
    ".yml": "#",
    ".yaml": "#",
    ".toml": "#",
    ".js": "//",
    ".ts": "//",
    ".go": "//",
    ".rs": "//",
    ".c": "//",
    ".h": "//",
    ".java": "//",
}
CITE_EXTENSIONS = ("py", "md", "sh", "yml", "yaml", "toml", "json", "js", "ts", "go", "rs", "c", "h", "java", "txt")  # extensions a cite path may end in
CITE_QUOTED_RE = re.compile(r"`[^`]*`|\"[^\"]*\"|'[^']*'")  # a quoted span: backticks or double or single quotes
CITE_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:" + "|".join(CITE_EXTENSIONS) + r")):(\d+)(?:-(\d+))?\b")  # path:line or path:start-end
CITE_BACKTICK_FILE_RE = re.compile(r"`((?:[\w.-]+/)*[\w-]+\.(?:" + "|".join(CITE_EXTENSIONS) + r"))`")  # a backticked file name with a cite extension, no line
CITE_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")  # URLs are not cites: `https://host:80/x`
CITE_FENCE_RE = re.compile(r"^\s*```")  # a fenced block opens or closes on this line
CITE_SYMBOL_BEFORE_RE = re.compile(r"`([A-Za-z_]\w*)`[\s(]*$")  # a backticked symbol right before a cite
CITE_DEF_RE = r"\b(?:def|class|function|func|fn|const|let|var)\s+{sym}\b"  # template: a definition of the symbol
CITE_WORD_RE = r"\b{sym}\b"  # template: any occurrence of the symbol
CITE_FIXED_WORDS = (
    "was",
    "old",
    "fixed",
    "stale",
    "previously",
)  # a cite in backticks or quotes on a line with one of these words is quoted as already fixed: not checked
CITE_CHECK_KEY = "cite_check"  # pipeline.json key: the cite check runs after the Test lines are green (testrun.py)
CITE_CHECK_DEFAULT = True  # the key when pipeline.json does not set it
CITE_DIFF_MAX_CHARS = 10**7  # the cite check reads the whole committed diff: a cut would hide the cites past it
CITE_SPLIT_MIN_REMOVED = 40  # a file whose diff removes at least this many lines is split: its report cites by line are not checked (cite_check.split_files)
CITE_MSG_PATH_RE = re.compile(r" cites ([^\s:]+):")  # the path of a cite in a cite check message
DOCS_IMPACT_KEY = "docs_impact_gate"  # pipeline.json key: the docs impact check runs after the cite check (docs_impact.py)
DOCS_IMPACT_DEFAULT = True  # the key when pipeline.json does not set it
DOCS_IMPACT_STAGE_DEFAULT_KEY = "default"  # the dict form of DOCS_IMPACT_KEY: the switch of every stage it does not name
DOCS_IMPACT_EXEMPT_KEY = "docs_impact_exempt"  # pipeline.json key: globs of docs the docs impact check never flags as stale
DOCS_IMPACT_SYMBOL_MIN = 3  # a top-level def or class name shorter than this is not read as a symbol a doc can cite
DOCS_IMPACT_GENERIC_SYMBOLS = frozenset({"main"})  # def names every script has: a doc citing another file's `main` is not made stale by this diff
DOCS_IMPACT_DEF_RE = re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")  # a column-0 def, async def or class line; group 1 = the name
DOCS_IMPACT_HEADER_RE = re.compile(r"^@@ .* @@ ?(.*)$")  # a hunk header; group 1 = the function context git puts after it
DOCS_IMPACT_PATH_RE = r"(?<![\w/.-]){path}(?![\w/-]|\.\w)"  # template: a changed path word-bounded in a doc
DOCS_IMPACT_TICK_RE = r"`{sym}`"  # template: a symbol in backticks in a doc
DOCS_GATE_OFF_RE = re.compile(
    r"^\s*Docs gate\s*:\s*off\b", re.I | re.M
)  # a TASKS.md line that switches the docs impact check off for its stage (docs_impact.plan_off)
DOC_CITES_KEY = "docs_cite_final"  # pipeline.json key: the cite check over the whole docs folder runs at every stage end; False = no step
DOC_CITES_DEFAULT = True  # the key when pipeline.json does not set it
DOC_CITES_STEP = "docs-cite-final"  # id of the script step that runs the whole-docs cite check (phases.audit_phases)
DOC_CITES_FLAG = "--doc-cites"  # steps_finaltest.py mode: check every doc under docs_dir, no --report needed
DOCS_DIR_KEY = "docs_dir"  # pipeline.json key: folder below the repo that holds the docs


def docs_dir_of(cfg: dict) -> str:
    """-> the docs folder of pipeline.json `cfg` (DOCS_DIR_KEY), "" when unset: the docs checks are off."""
    return str(cfg.get(DOCS_DIR_KEY) or "")
