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
CITE_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:" + "|".join(CITE_EXTENSIONS) + r")):(\d+)(?:-(\d+))?\b")  # path:line or path:start-end
CITE_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")  # URLs are not cites: `https://host:80/x`
CITE_FENCE_RE = re.compile(r"^\s*```")  # a fenced block opens or closes on this line
CITE_SYMBOL_BEFORE_RE = re.compile(r"`([A-Za-z_]\w*)`[\s(]*$")  # a backticked symbol right before a cite
CITE_DEF_RE = r"\b(?:def|class|function|func|fn|const|let|var)\s+{sym}\b"  # template: a definition of the symbol
CITE_WORD_RE = r"\b{sym}\b"  # template: any occurrence of the symbol
CITE_CHECK_KEY = "cite_check"  # pipeline.json key: the cite check runs after the Test lines are green (testrun.py)
CITE_CHECK_DEFAULT = True  # the key when pipeline.json does not set it
CITE_DIFF_MAX_CHARS = 10**7  # the cite check reads the whole committed diff: a cut would hide the cites past it
