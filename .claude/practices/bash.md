# Bash scripts: gates, commands, traps
Practical notes for shell scripts (`scripts/*.sh`, hooks). The project's own CLAUDE.md / AUDIT.md win over this file.

## Gates
1. Syntax: `bash -n script.sh`.
2. Lint: `shellcheck script.sh` (clean, or a reviewed `# shellcheck disable=SCxxxx  # reason`).
3. Format: `shfmt -d script.sh` when the project uses it.
4. Run the script's own test or a dry run through qrun: `cs-qrun -- bash scripts/<name>.sh --dry-run`.

## Script header
```
#!/usr/bin/env bash
set -euo pipefail
```
Resolve the repo root from the script path (`cd "$(dirname "${BASH_SOURCE[0]}")/.."`), never from the caller's cwd.

## Commands
- Run a script as `bash scripts/x.sh` or `scripts/x.sh` (the headless allowlist covers both); do not wrap it in `sh -c`.
- Test one function: source the script behind a `[[ "${BASH_SOURCE[0]}" == "$0" ]]` main guard.
- One qrun call per Bash call. After qrun only `&& <another cs-qrun call>` or `&& <read-only command>` (ls, echo, wc, cat, head, tail, test), e.g. `cs-qrun -- pytest && ls out`; `;`, `|`, `||` or a newline after it are denied. Do not put `env` or `VAR=value` before a test command.

## Headless denials
`cd D && git ...` (or `;`, pipes after git) is rewritten by cmd_guard to `git -C D ...`; still denied: a cd whose later non-git segment needs the cwd (relative path, no args), `~`, `$VAR` or quoted targets (use `git -C D` yourself). Avoid (headless denies them):
- Shell variables for paths (`W=/p; ls $W/x`): use the literal path or `git -C`.
- `sed -n 'A,Bp' file` (print lines) is allowed; every other sed form is denied (cmd_guard). `sed -i` and other edits: use the Edit tool.
- Run a script you wrote with Write only from the scratch dir: `python3 -I $PIPELINE_SCRATCH_DIR/x.py` is allowed there (literal absolute path), a script elsewhere is denied. `unzip`/`tar -x` are denied: `python3 -I -c "import zipfile;zipfile.ZipFile(\"k.zip\").extractall(\"<scratch>/x\")"`.
- awk programs with `{}` blocks: use `python3 -I - <<'EOF'`, or jq/grep/cut.
- `find /` over the whole filesystem.
- `rm -rf DIR` is fine for your own tmp data, but not when your shell is inside DIR (or below it): cd out first, or remove DIR's contents (`DIR/*`).

Denial report: `python3 -I .claude/tools/retro/denials.py --days 5` counts denied Bash calls in the transcripts; `--log <denials.jsonl>` counts the logged rows by rule. This file is the single source for these command rules; briefs cite it instead of repeating them.

## JSON with jq
Read JSON/JSONL with `jq`, not python/node one-offs. Allowed without approval: jq, grep, sort, uniq, cut, head, wc, cat, ls. No `>` redirects, one command per Bash call (qrun chain forms above excepted). jq cannot write files: a table for a later step comes from a script task, not from `jq ... > out`. Recipes (run on `f.jsonl` rows `{id,st,note}` and `f.json` `{items:{k:{ok,bad}}}`):
- One row by id: `jq -c 'select(.id=="sd-170")' f.jsonl`
- Length: `jq '.items|length' f.json`; rows in a JSONL: `jq -s 'length' f.jsonl`
- TSV of fields: `jq -r '.items|to_entries[]|[.key,.value.ok,.value.bad]|@tsv' f.json`
- Count rows by field: `jq -s -c 'group_by(.st)|map({st:.[0].st,n:length})' f.jsonl`
- Ids where a field is missing or empty: `jq -r 'select((.note//"")=="")|.id' f.jsonl`
- Join by id (`--argfile` is deprecated; B is a JSON array of `{id,v}`): `jq -n -c --slurpfile a B.json --slurpfile b f.jsonl '($a[0]|map({(.id):.v})|add) as $v|$b[]|{id,st,v:$v[.id]}'`
- Column to a pipe: `jq -r .st f.jsonl | sort | uniq -c`

## Traps
- Quote every expansion: `"$var"`, `"${arr[@]}"`. Unquoted paths break on spaces and globs.
- `set -e` does not fire inside `if`, `&&`/`||` chains, or command substitution in `local x=$(cmd)`; split declaration and assignment.
- A pipe returns the last command's status unless `pipefail` is set; `cmd | tail` hides a failure.
- `cd` can fail: use `cd dir || exit 1`, or `cd` in a subshell `( cd dir && cmd )`.
- Temp files: `mktemp -d` plus `trap 'rm -rf "$tmp"' EXIT`; never a fixed `/tmp/name`.
- `rm -rf "$dir/"` with an empty `$dir` deletes `/`: use `"${dir:?}"`.
- `echo` mangles `-n`, `-e` and backslashes: use `printf '%s\n' "$x"`.
- Read lines with `while IFS= read -r line`; do not loop over `$(ls)` or `$(cat file)`.
- Portability: `[[ ]]`, arrays and `local` are bash only; a `#!/bin/sh` script must avoid them.
