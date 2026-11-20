ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: rustup (stable), clippy, rust-analyzer, wasm32 target, wasm-pack, cargo-audit, cargo-deny, cargo-machete, cargo-llvm-cov, cargo-bloat, ruff, gcc

# Rust toolchain (for wasm work): rustup + stable + clippy + wasm32-unknown-unknown, and
# wasm-pack. Installed system-wide under /opt so it works regardless of $HOME
# (the entrypoint/bind mounts own /root). gcc below is the linker.
# wasm-pack ships prebuilt binaries for x86_64 only; arm64 builds it from source.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Pinned 2026-11-05; empty WASM_PACK_VERSION = latest GitHub release.
ARG WASM_PACK_VERSION=0.15.1
ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    PATH=/opt/cargo/bin:$PATH
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable \
    && rustup component add clippy rust-analyzer \
    && rustup target add wasm32-unknown-unknown \
    && WASM_PACK_VERSION="${WASM_PACK_VERSION:-$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/rustwasm/wasm-pack/releases/latest | sed 's|.*/tag/v||')}" \
    && case "$(dpkg --print-architecture)" in \
        amd64) \
            curl -fsSL -o /tmp/wasm-pack.tar.gz \
              "https://github.com/rustwasm/wasm-pack/releases/download/v${WASM_PACK_VERSION}/wasm-pack-v${WASM_PACK_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
            && tar -xzf /tmp/wasm-pack.tar.gz -C /tmp \
            && install -m 755 "/tmp/wasm-pack-v${WASM_PACK_VERSION}-x86_64-unknown-linux-musl/wasm-pack" /opt/cargo/bin/wasm-pack \
            && rm -rf /tmp/wasm-pack* ;; \
        *) cargo install wasm-pack --version "${WASM_PACK_VERSION}" --locked ;; \
    esac \
    && rustc --version && cargo --version && cargo clippy --version && wasm-pack --version

# Vetted audit tools (see .claude/EXTRA-TOOLS.md), built from source with the lockfile.
# Both fetch the RustSec advisory DB at run time; neither does anything else on the network.
ARG CARGO_AUDIT_VERSION=0.22.2
ARG CARGO_DENY_VERSION=0.20.2
RUN cargo install --locked cargo-audit${CARGO_AUDIT_VERSION:+ --version $CARGO_AUDIT_VERSION} \
    && cargo install --locked cargo-deny${CARGO_DENY_VERSION:+ --version $CARGO_DENY_VERSION} \
    && rm -rf /opt/cargo/registry /opt/cargo/git

ARG CARGO_MACHETE_VERSION=0.9.2
ARG CARGO_LLVM_COV_VERSION=0.9.1
ARG CARGO_BLOAT_VERSION=0.12.1
RUN rustup component add llvm-tools-preview \
    && cargo install --locked "cargo-machete@${CARGO_MACHETE_VERSION}" "cargo-llvm-cov@${CARGO_LLVM_COV_VERSION}" "cargo-bloat@${CARGO_BLOAT_VERSION}" \
    && rm -rf /opt/cargo/registry /opt/cargo/git \
    && chown -R node:node /opt/cargo /opt/rustup \
    && cargo audit --version && cargo deny --version && cargo machete --version && cargo llvm-cov --version && cargo bloat --version

# ruff (Python lint/format, for the scripts that sit next to wasm projects). Same uv-tool
# layout as the base image's QA tools. Pinned 2026-11-05; same version as the base image.
ARG RUFF_VERSION=0.17.5
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "ruff${RUFF_VERSION:+==$RUFF_VERSION}" \
    && ruff --version
