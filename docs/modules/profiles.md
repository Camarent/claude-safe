---
paths: [profiles/]
summary: optional toolchain layers for the container image, one Dockerfile per profile, each stacked on the previous image
---
# profiles

## Purpose
Heavy toolchains kept out of the base image. Each `profiles/<name>.Dockerfile` adds one layer on top of a base image.
`run.sh` builds them as a chain and caches each result as `claude-safe:<profiles>`.

## Public surface
Seven profiles, by file name (`profiles/<name>.Dockerfile`):
- `audio` - `profiles/audio.Dockerfile:3` (ffmpeg, yt-dlp, faster-whisper)
- `cluster` - `profiles/cluster.Dockerfile:3` (docker daemon, kubectl, kind, helm, act, Slurm); start with `--with cluster`
- `cpp` - `profiles/cpp.Dockerfile:3` (g++, clang, clangd, clang-tidy, cppcheck, cmake, ninja, bear)
- `exttest` - `profiles/exttest.Dockerfile:3` (Firefox ESR, geckodriver, selenium, VS Code test host, Xvfb)
- `office` - `profiles/office.Dockerfile:3` (LibreOffice, pandoc, typst, tesseract, poppler)
- `postgres` - `profiles/postgres.Dockerfile:3` (PostgreSQL 17 binaries, pgvector, Python venv)
- `rust` - `profiles/rust.Dockerfile:3` (rustup stable, clippy, rust-analyzer, wasm-pack, cargo tools, ruff, gcc)

Each file has the same contract: `ARG BASE=claude-safe:latest` (`:1`), `FROM ${BASE}` (`:2`), `# desc: ...` (`:3`).
`run.sh:63` reads the `# desc:` line and prints it in the profile list (`run.sh:64`). Chain comment: `run.sh:167`.

## Key files
- `profiles/*.Dockerfile` - one layer each. Version pins are `ARG` lines near the top, e.g. `profiles/audio.Dockerfile:17` (`YT_DLP_VERSION`), `profiles/cluster.Dockerfile:13` (`KUBECTL_VERSION`), `profiles/exttest.Dockerfile:7` (`FIREFOX_VERSION`), `profiles/office.Dockerfile:25` (`PANDOC_VERSION`), `profiles/postgres.Dockerfile:24` (`FASTAPI_VERSION`).
- `Dockerfile` - base image (`FROM node:lts-trixie-slim`, `Dockerfile:5`); comment at `Dockerfile:4` points here.

## Invariants and gotchas
- Stacking order matters: `run.sh` passes `--build-arg BASE=<previous image>` (`run.sh:214`). A profile can only assume what earlier layers provide.
- An empty pin `ARG X=` means latest, not a pin. Pinned values are copied into `scripts/provision-dev-vm.sh`; keep them equal (test: `.claude/tools/tests/test_provision_script.py`).
- `office`: `--no-install-recommends` keeps the X/GNOME stack out (`profiles/office.Dockerfile:51`). Calibri/Cambria clones carlito/caladea must stay, or Office documents repaginate (`:52-53`).
- `postgres`: the server is not started or enabled. Tests start their own instance in a temp dir (`profiles/postgres.Dockerfile:5-7`).
- `exttest`: the VS Code test host runs with `--no-sandbox`, since user namespaces are unavailable (`profiles/exttest.Dockerfile:29`).
- `audio`: Whisper models are not baked in; they download on first use (`profiles/audio.Dockerfile:14-15`).
- `cluster`: runs a privileged container with its own dockerd; the host Docker is not reachable (`profiles/cluster.Dockerfile:5-6`).

## Depends on / used by
- Depends on: `Dockerfile` (base image), `claude-safe:latest` (`BASE` default), docker.
- Used by: `run.sh` (builds the chain, `--rebuild` rebuilds all), `README.md` (profile contract, line 89), `scripts/provision-dev-vm.sh` (copies the pins).
