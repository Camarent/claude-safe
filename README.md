# claude-safe

Claude Code in a throwaway Docker container. Nothing on the host is reachable
except the folder you explicitly mount.

    ./run.sh            # mount scratch/ only
    ./run.sh --here     # mount $PWD
    ./run.sh --rebuild  # rebuild base + all profile images first

## Browsing

The container ships its own Chrome, driven through Google's `chrome-devtools-mcp`.
Claude can navigate, click, fill forms, screenshot and read the DOM *after* JS has
run — which is what makes dynamically rendered pages (Maps reviews, SPAs) readable.

Chrome runs **headful on a virtual display (Xvfb)**, not `--headless`: headless is
the loudest bot-detection signal there is. Verified in-container: `navigator.webdriver`
is false, no "Headless" in the UA, plugins present.

The profile lives in `browser-profile/` on the host and is mounted into the
container, so cookies and logins survive `--rm`. Two consequences worth knowing:

- **Consent walls are a one-time cost.** Google shows an interstitial before Maps.
  Ask Claude to click "Accept all" once; the `SOCS` cookie persists and every later
  session goes straight through.
- **Let the browser close cleanly.** Chrome flushes its cookie DB on shutdown. If a
  session is killed hard, the last cookies are lost — and the profile keeps a stale
  `SingletonLock`. The entrypoint clears those locks on start, so this self-heals.

Delete `browser-profile/` to start from a clean browser.

## Token filtering

[snip](https://github.com/edouard-claude/snip) (MIT, Go) installs as a PreToolUse
hook on Bash. It runs each shell command itself, passes the output through a
declarative YAML filter, and hands Claude the condensed result. 132 filters ship
with it — git, gh, grep, rg, find, ls, curl, docker, make and the usual test
runners. Measured in this image:

| command | raw | filtered | saved |
|---|---|---|---|
| `grep -rn chrome .` | 390 KB | 5.3 KB | 99% |
| `find . -type f` | 128 KB | 1.9 KB | 99% |
| `git diff HEAD~1` | 7.4 KB | 369 B | 96% |
| `git log` | 1.5 KB | 173 B | 89% |
| `curl example.com` | 559 B | 121 B | 79% |

Commands with no matching filter, or whose output is already small, pass through
untouched. A non-zero exit keeps its real stderr and exit code, so failures stay
readable; raw output is also saved under `~/.local/share/snip/tee/` in the
container and its path is printed to stderr.

Two things to know:

- **It rewrites commands.** `git status` is run as `git status --porcelain`,
  `go test` as `go test -json`. A summary line names any injected flags.
- **A clean `git status` comes back empty** rather than "nothing to commit". The
  filter has no `on_empty` case for it, so silence means clean.

`./run.sh --no-snip` takes it out of the loop entirely.

Scope: the container browses from your host's IP with a fresh profile. It is not a
way around hard bot walls — if a site refuses it, open that page in your own Chrome.

## Profiles (optional toolchains)

The base image holds what every session needs: node, git, gh, Chrome, python/uv with the
QA tools (coverage, pip-audit, bandit, vulture, lizard, ruff, pytest + pytest-cov), JS audit tools in `/opt/jstools`
(knip, jscpd, madge, eslint), ctags, pyright and tsc --lsp (TypeScript 7), snip,
gitleaks, claude-code. Heavier toolchains are separate layers in `profiles/`
(`claude-safe --help` lists them with their `# desc:` lines):

| profile | contents |
|---|---|
| `cpp`    | g++, clang, clangd, clang-tidy, cppcheck, cmake, make, bear |
| `rust`   | gcc, rustup (stable), clippy, rust-analyzer, `wasm32-unknown-unknown`, wasm-pack, cargo-audit, cargo-deny, cargo-machete, cargo-llvm-cov, ruff |
| `office` | LibreOffice, pandoc, typst, tesseract (eng+fra), poppler, Office fonts |
| `audio`  | ffmpeg, yt-dlp, faster-whisper (`/opt/audio-venv`; models download on first use) |
| `cluster` | docker (own dockerd), act, kubectl, kind, helm, one-node Slurm + munge (+0.6 GB); added by `--with cluster` |
| `postgres` | PostgreSQL 17 (`initdb`, `pg_ctl`, `postgres`, `psql` on PATH, service not started), pgvector, `/opt/pg-venv` with FastAPI, uvicorn, SQLAlchemy, psycopg[binary], httpx |

    claude-safe --with rust
    claude-safe --here --with rust,cpp

Each combination is built once and cached as `claude-safe:<profiles>` (sorted, e.g.
`claude-safe:cpp-rust`); shared prefix layers are reused. `--rebuild` rebuilds the
base image, every available profile, and any combination image built earlier, so remove
combinations you no longer use (`docker rmi claude-safe:<tag>`) first. Add a profile by dropping
`profiles/<name>.Dockerfile` that starts with `ARG BASE=claude-safe:latest` / `FROM ${BASE}` plus a `# desc: ...` line.

Rust lives in `/opt/rustup` + `/opt/cargo`; pin wasm-pack with
`--build-arg WASM_PACK_VERSION=x.y.z`. Chrome and Node are in the base, so
`wasm-pack test --headless --chrome` works with the `rust` profile.

Extra tools beyond the image are listed, vetted and pinned in `.claude/EXTRA-TOOLS.md`.
When you bump a baked-in tool there, bump its `ARG` in the Dockerfile too.

## Users and ownership

The container starts as root only long enough to update Claude Code, install vetted `apt`
rows from `EXTRA-TOOLS.md` and re-own leftovers, then drops to the unprivileged `node` user
mapped to your host uid/gid (`HOME` stays `/root`, owned by that user). Files created in the
mounted folders belong to you on the host. Chrome runs with `--no-sandbox` because the
container does not allow user namespaces.

## Pipeline modes
    ./run.sh --here                 Claude Code on your subscription
    ./run.sh --here                 the same, plus the local models of the model-host project when they are running (see "Local models")
Details and status of the thin pipeline: `.claude/practices/orch-pipeline.md`. Tests of the tools: run them inside the container.

Dev reports: `cmd_report` takes a dev's report from its job worktree (`.work/<stage>/reports/report-<id>.md`) when the main report has no `VERDICT:` line. Chosen over an absolute report path plus a guard allowlist: the guard (maincheck) blames any write to the main checkout, and a dev may still write a relative path, so the fallback covers both. A main report that has a VERDICT line is never overwritten.

## Local cluster (`--with cluster`)

`./run.sh --here --with cluster` adds the `cluster` profile and starts, as root in the entrypoint, an own `dockerd`
(docker-in-docker; the host Docker socket is NOT mounted) and a one-node Slurm (`munged`, `slurmctld`, `slurmd`,
node detected with `slurmd -C`). The container runs `--privileged`, which loosens isolation: use it only for this work.
Images pulled by Docker/kind/act are kept in the volume `claude-safe-docker` (`docker volume rm` to reset).
kind needs about 2-4 GB RAM, so stop the local model first (`modelctl use none` in model-host) unless you have enough RAM. Checked: `docker run`, `sbatch -W`/`srun`, `kind create cluster`.
Logs: `/tmp/dockerd.log`, `/var/log/slurm/`.

## Local models (model-host)

Ollama and Strata no longer live in this image. They run from the separate project
[model-host](https://github.com/Camarent/model-host) (a Compose project with its own `modelctl`, `~/workspace/model-host` by
default, or `MODEL_HOST_DIR`). Start an engine there (`bin/modelctl up` for Ollama, `bin/modelctl use strata` for Strata); one engine
holds the GPU at a time.

`run.sh` connects by itself, nothing to flag: when the Docker network `model-net` exists, the container joins it, and for each engine
running at that moment it sets `OLLAMA_URL=http://model-host-ollama:11434` and `STRATA_URL=http://model-host-strata:8080` plus
`STRATA_KEY` (read from model-host's generated `data/strata.key`). Without model-host the container starts as before.
An engine started after the container has no variables in it: restart the container.

The pipeline can no longer start Strata (it has no access to the host): `preflight()` in `.claude/tools/search/strata.py` skips the
local-model steps when it does not answer. `--with strata` and `--with ollama` are gone and say where to go.
Leftover directories `strata-data/` and `ollama-data/` from the old layout are git-ignored; point model-host's `STRATA_DATA` at
`strata-data/` to reuse the model files.

## Development workflow

`master` is the only long-lived branch. Work happens on a short-lived branch, goes through a pull request, and is
squash-merged once the GitHub `ci` workflow (`.github/workflows/ci.yml`: ruff, mypy, tests with coverage floor,
vulture, bandit, complexity gate, gitleaks over the full history) is green. Before opening the PR run the same
checks locally with `scripts/dev.sh` (`check`, `cov`, `dead`, `sec`, `ccn`); `scripts/release.sh` adds the
container suite, which CI does not run (it needs the `claude-safe:rust` image).

Deferred (decided, not done):
- Devs using `lp nav` more: the audit finds file rereads in 2 of 303 sessions, too little value; revisit after phases 1 to 3.
- Removing the local-model gate judge step when the model is down: it costs 0 s and only adds log noise; not worth a code path.

## Pipeline hardening notes

Decisions of the pipeline-hardening stage, one section per problem: `docs/decisions.md`.

- Sync `~/.claude` from one ref: `scripts/sync-claude-config.sh [REF]` (REF defaults to HEAD). It refuses while a wave driver runs, works in a detached worktree, and never passes `host_agents.py --apply`.
- Retry without a new dev run: `orch.py retry --verify` runs the task's Test lines and Done checks on the existing job commit and writes the report. Red records nothing.
- Cleanup step (finish phase): removes merged or gone job worktrees and run temp dirs. Preview with `python3 -I .claude/tools/pipeline/cleanup.py --dry-run`; it changes nothing.

## Setting up a new machine

1. Clone the repo, then `scripts/dev.sh sync` (needs `uv` and a system Python 3.11+).
2. Docker host (the PC with a GPU): `./run.sh --rebuild`, add `--with cluster` as needed; the local models come from model-host.
3. No-container host (a VM): install the toolchains directly with `scripts/provision-dev-vm.sh` (`--list` shows the steps, `--dry-run`
   shows what would change without sudo). The
   `cluster` profile is PC-only: it needs a privileged container.
4. Per-machine values stay out of git: host paths come from `~` or `$HOME` in `.claude/paths.json`, tokens from the
   environment or an untracked `~/.config/secrets.env` (`chmod 600`), never from the repo.
5. Privacy blocklist: copy `.claude/git/privacy.gitconfig.example` to `.claude/git/privacy.gitconfig` (untracked) and set
   your own regex; optionally add the same regex as repo secret `PRIVACY_BLOCKLIST` for the CI `privacy` job.
