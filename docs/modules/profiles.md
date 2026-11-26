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
- `rust` - `profiles/rust.Dockerfile:4` (rustup stable, clippy, rust-analyzer, wasm-pack, cargo tools, ruff, gcc)

Each file starts with `ARG BASE=claude-safe:latest` and `FROM ${BASE}` (cpp `:1`/`:2`, rust `:2`/`:3`), then a `# desc: ...` line (cpp `:3`, rust `:4`).
`run.sh:67` reads the `# desc:` line and prints it in the profile list (`run.sh:68`). Chain comment: `run.sh:163`.

## Key files
- `Dockerfile` - base image (`FROM node:lts-trixie-slim`, `Dockerfile:6`); comment at `Dockerfile:4` points here.

## Invariants and gotchas
- Stacking order matters: `run.sh` passes `--build-arg BASE=<previous image>` (`run.sh:211`). A profile can only assume what earlier layers provide.

## Depends on / used by
- Depends on: `Dockerfile` (base image), `claude-safe:latest` (`BASE` default), docker.
- Used by: `run.sh` (builds the chain, `--rebuild` rebuilds all), `README.md` (profile contract, line 108).
