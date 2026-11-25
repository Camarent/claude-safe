---
paths: [profiles/]
summary: optional toolchain layers for the container image, one Dockerfile per profile, each stacked on the previous image
---
# profiles

## Purpose
Heavy toolchains kept out of the base image. Each `profiles/<name>.Dockerfile` adds one layer on top of a base image.
`run.sh` builds them as a chain and caches each result as `claude-safe:<profiles>`.

## Public surface
Two profiles, by file name (`profiles/<name>.Dockerfile`):
- `cpp` - `profiles/cpp.Dockerfile:3` (g++, clang, clangd, clang-tidy, cppcheck, cmake, ninja, bear)
- `rust` - `profiles/rust.Dockerfile:3` (rustup stable, clippy, rust-analyzer, wasm-pack, cargo tools, ruff, gcc)

Each file has the same contract: `ARG BASE=claude-safe:latest` (`:1`), `FROM ${BASE}` (`:2`), `# desc: ...` (`:3`).
`run.sh:62` reads the `# desc:` line and prints it in the profile list (`run.sh:63`). Chain comment: `run.sh:157`.

## Key files
- `Dockerfile` - base image (`FROM node:lts-trixie-slim`, `Dockerfile:5`); comment at `Dockerfile:4` points here.

## Invariants and gotchas
- Stacking order matters: `run.sh` passes `--build-arg BASE=<previous image>` (`run.sh:204`). A profile can only assume what earlier layers provide.

## Depends on / used by
- Depends on: `Dockerfile` (base image), `claude-safe:latest` (`BASE` default), docker.
- Used by: `run.sh` (builds the chain, `--rebuild` rebuilds all), `README.md` (profile contract, line 85).
