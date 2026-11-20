"""Draft `audit-tools` block for a project that has none: detect the languages by marker files, list the usual free tools for each.

The draft is a proposal. The pipeline never installs from it; the tools still go through TOOL-REQUEST vetting (practices/audits.md)."""

import os

# marker files -> (language, [audit-tools lines: name | cwd | command | parser])
STARTERS = (
    (
        ("pyproject.toml", "setup.py", "requirements.txt"),
        "Python",
        [
            "ruff | . | ruff check --output-format json . | ruff",
            "vulture | . | vulture . --min-confidence 80 | vulture",
            "bandit | . | bandit -r . -f json -q | bandit",
        ],
    ),
    (
        ("Cargo.toml",),
        "Rust",
        ["clippy | . | cargo clippy --workspace --all-targets --message-format=json | clippy", "cargo-audit | . | cargo audit --json | cargo-audit"],
    ),
    (
        ("package.json",),
        "JavaScript",
        ["eslint | . | /opt/jstools/node_modules/.bin/eslint -f json . | eslint", "npm-audit | . | npm audit --json | npm-audit"],
    ),
    (
        ("CMakeLists.txt", "Makefile", "meson.build", "compile_commands.json"),
        "C++",
        [
            "cppcheck | . | cppcheck --enable=warning,style,performance --inline-suppr -q --template='{file}:{line}:{severity}:{id}:{message}' . | cppcheck",
            "clang-tidy | . | clang-tidy -p . --quiet $(git ls-files '*.cpp' '*.cc' '*.cxx') | clang-tidy",
            "lizard | . | lizard -w . | lizard",
        ],
    ),
)


def detect(repo):
    """-> [(language, [lines])] for every language whose marker file is in the repo root."""
    return [(lang, lines) for marks, lang, lines in STARTERS if any(os.path.exists(os.path.join(repo, m)) for m in marks)]


def draft(repo):
    """-> the AUDIT.md block text to propose, or "" when no language is recognised."""
    found = detect(repo)
    if not found:
        return ""
    body = "\n".join(f"# {lang}\n" + "\n".join(lines) for lang, lines in found)
    return "```audit-tools\n" + body + "\n```\n"
