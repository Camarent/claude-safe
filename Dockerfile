# Base image: node, git, gh, browser, python/uv + QA tools, ctags, JS/Python language
# servers, snip, gitleaks, claude-code.
# Optional toolchains (cpp, rust, office) are layered on top by run.sh --with,
# see profiles/*.Dockerfile.
FROM node:lts-trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    ripgrep \
    procps \
    less \
    jq \
    gnupg \
    zstd \
    xvfb \
    python3 \
    python3-venv \
    python3-pip \
    universal-ctags \
    fontconfig fonts-dejavu-core fonts-liberation2 fonts-noto-core fonts-noto-color-emoji \
    fonts-noto-cjk \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Browser stack for reading dynamically-rendered pages (Maps reviews, SPAs, and
# anything that only exists after JS runs).
#
# Headful Chrome under Xvfb, NOT --headless: headless is the single loudest
# bot-detection signal, and a virtual display costs almost nothing here.
# Google ships Chrome for linux amd64 only, so arm64 falls back to Chromium.
RUN apt-get update \
    && case "$(dpkg --print-architecture)" in \
        amd64) \
            curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
              | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
            && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
              > /etc/apt/sources.list.d/google-chrome.list \
            && apt-get update \
            && apt-get install -y --no-install-recommends google-chrome-stable \
            && ln -sf /usr/bin/google-chrome-stable /usr/local/bin/browser ;; \
        *) \
            apt-get install -y --no-install-recommends chromium \
            && ln -sf /usr/bin/chromium /usr/local/bin/browser ;; \
    esac \
    && rm -rf /var/lib/apt/lists/*

# Google's own CDP-backed MCP server: navigate, click, read the DOM after
# hydration, screenshot, tail the console.
# Pinned 2026-11-05; bump the ARG to update.
ARG CHROME_DEVTOOLS_MCP_VERSION=1.13.0
RUN npm install -g "chrome-devtools-mcp@${CHROME_DEVTOOLS_MCP_VERSION}"

COPY mcp-servers.json /etc/claude/mcp-servers.json

ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# snip — filters verbose shell output before it reaches the context window.
# It installs as a PreToolUse hook on Bash: it runs the command itself, applies a
# declarative YAML filter, and returns the condensed result. Measured in this
# image: find -type f 127KB -> 1.9KB, grep -rn 390KB -> 5.3KB, git diff -96%.
# Non-zero exits keep their real stderr and exit code, so failures stay readable.
# Pinned 2026-11-05; empty SNIP_VERSION = latest GitHub release.
ARG SNIP_VERSION=0.26.0
RUN snipVersion="${SNIP_VERSION:-$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/edouard-claude/snip/releases/latest | sed 's|.*/tag/v||')}" \
    && case "$(dpkg --print-architecture)" in \
        amd64) snipArch=amd64 ;; \
        arm64) snipArch=arm64 ;; \
        *) echo "unsupported arch for snip: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/snip.tar.gz \
       "https://github.com/edouard-claude/snip/releases/download/v${snipVersion}/snip_${snipVersion}_linux_${snipArch}.tar.gz" \
    && tar -xzf /tmp/snip.tar.gz -C /tmp \
    && install -m 755 /tmp/snip /usr/local/bin/snip \
    && rm -f /tmp/snip.tar.gz /tmp/snip

# gitleaks — scans repos/files for committed secrets (`gitleaks detect`, `gitleaks dir`).
# Pinned 2026-11-05; empty GITLEAKS_VERSION = latest GitHub release.
ARG GITLEAKS_VERSION=8.30.1
RUN gitleaksVersion="${GITLEAKS_VERSION:-$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/gitleaks/gitleaks/releases/latest | sed 's|.*/tag/v||')}" \
    && case "$(dpkg --print-architecture)" in \
        amd64) gitleaksArch=x64 ;; \
        arm64) gitleaksArch=arm64 ;; \
        *) echo "unsupported arch for gitleaks: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac \
    && mkdir /tmp/gitleaks \
    && curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${gitleaksVersion}/gitleaks_${gitleaksVersion}_linux_${gitleaksArch}.tar.gz" \
       | tar -xz -C /tmp/gitleaks gitleaks \
    && install -m 755 /tmp/gitleaks/gitleaks /usr/local/bin/gitleaks \
    && rm -rf /tmp/gitleaks \
    && gitleaks version

# Python QA tools, each isolated by uv in /opt/uv-tools with
# its bin linked into /usr/local/bin, so they are on PATH and live outside every project.
# python3-venv + pip because PEP 668 blocks system pip, so projects make their own venvs.
# Pinned (2026-11-05): coverage/pip-audit/bandit/vulture/lizard match the EXTRA-TOOLS.md rows; bump both together.
ARG COVERAGE_VERSION=7.17.3
ARG PIP_AUDIT_VERSION=2.10.1
ARG BANDIT_VERSION=1.9.4
ARG VULTURE_VERSION=2.16
ARG LIZARD_VERSION=1.26.1
ARG RUFF_VERSION=0.17.5
ARG PYTEST_VERSION=9.2.0
ARG PYTEST_COV_VERSION=7.1.0
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin sh -c '\
    uv tool install "coverage${COVERAGE_VERSION:+==$COVERAGE_VERSION}" \
    && uv tool install "pip-audit${PIP_AUDIT_VERSION:+==$PIP_AUDIT_VERSION}" \
    && uv tool install "bandit${BANDIT_VERSION:+==$BANDIT_VERSION}" \
    && uv tool install "vulture${VULTURE_VERSION:+==$VULTURE_VERSION}" \
    && uv tool install "lizard${LIZARD_VERSION:+==$LIZARD_VERSION}" \
    && uv tool install "ruff${RUFF_VERSION:+==$RUFF_VERSION}" \
    && uv tool install "pytest${PYTEST_VERSION:+==$PYTEST_VERSION}" --with "pytest-cov${PYTEST_COV_VERSION:+==$PYTEST_COV_VERSION}"' \
    && coverage --version | head -1 && pip-audit --version && bandit --version | head -1 \
    && vulture --version && lizard --version && ruff --version && pytest --version && pytest --help | grep -q -- --cov && ctags --version | head -1

# Language servers for code navigation (go to definition, find references, symbols).
# JS/TS and Python live here so every profile has them; clangd is in profiles/cpp and
# rust-analyzer in profiles/rust. typescript 7 is the native port: its own LSP is `tsc --lsp --stdio` (no tsserver.js, no typescript-language-server). pyright (Microsoft) is the Python server.
ARG PYRIGHT_VERSION=1.1.414
ARG TYPESCRIPT_VERSION=7.0.2
RUN npm install -g "pyright@${PYRIGHT_VERSION}" "typescript@${TYPESCRIPT_VERSION}" \
    && pyright --version && tsc --version

# Semantic code search (local.py find): fastembed (Qdrant, ONNX, CPU) + the small bge-small-en-v1.5 model, baked in so find works offline.
# Own venv so the system python stays clean; local.py re-executes under it. Version pinned to what was tested.
ARG FASTEMBED_VERSION=0.9.1
ENV FASTEMBED_CACHE_PATH=/opt/fastembed-cache HF_HUB_OFFLINE=1
RUN uv venv -q /opt/ask-venv && uv pip install -q --python /opt/ask-venv/bin/python "fastembed==${FASTEMBED_VERSION}" numpy \
    && HF_HUB_OFFLINE=0 /opt/ask-venv/bin/python -c "from fastembed import TextEmbedding; list(TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed-cache').embed(['ok']))" \
    && chmod -R a+rX /opt/ask-venv /opt/fastembed-cache

# JS audit tools (EXTRA-TOOLS.md rows, versions pinned to the vetted ones). xtools.py finds them in
# /opt/jstools/node_modules/.bin, so a session only installs a row that is newer or missing.
ARG KNIP_VERSION=6.45.1
ARG JSCPD_VERSION=5.4.3
ARG MADGE_VERSION=8.0.0
ARG ESLINT_VERSION=10.15.0
RUN npm i --prefix /opt/jstools --ignore-scripts "knip@${KNIP_VERSION}" "jscpd@${JSCPD_VERSION}" "madge@${MADGE_VERSION}" "eslint@${ESLINT_VERSION}" \
    && chown -R node:node /opt/jstools \
    && /opt/jstools/node_modules/.bin/knip --version && /opt/jstools/node_modules/.bin/eslint --version

# kata tooling (base: Python and JS go here, other toolchains in profiles).
# mypy and the Hugging Face CLI (`hf`) are isolated by uv like the QA tools; esbuild, web-ext (run/lint a Firefox
# extension) and vsce (package a VS Code extension) are global npm tools. Pinned 2026-11-11.
ARG MYPY_VERSION=2.4.1
ARG HUGGINGFACE_HUB_VERSION=2.4.0
ARG ESBUILD_VERSION=0.28.2
ARG WEB_EXT_VERSION=10.8.0
ARG VSCE_VERSION=4.1.1
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin sh -c '\
    uv tool install "mypy==${MYPY_VERSION}" \
    && uv tool install "huggingface_hub[cli]==${HUGGINGFACE_HUB_VERSION}"' \
    && npm install -g "esbuild@${ESBUILD_VERSION}" "web-ext@${WEB_EXT_VERSION}" "@vscode/vsce@${VSCE_VERSION}" \
    && mypy --version && (hf version || hf --help | head -1) && esbuild --version && web-ext --version && vsce --version

RUN npm install -g "@anthropic-ai/claude-code@latest"

# `lp` = short alias for the code-navigation / local-model entry point: every argument is forwarded
# unchanged, so `lp find "q"` is `python3 -I /root/.claude/tools/search/local.py find "q"` (the tool itself
# is in the mounted .claude, so tool updates need no rebuild).
RUN printf '#!/bin/sh\nexec python3 -I /root/.claude/tools/search/local.py "$@"\n' > /usr/local/bin/lp && chmod 755 /usr/local/bin/lp

# The cs-* wrappers (paths.json) live in the mounted .claude/bin; only that folder is on the container PATH, never bin-host.
ENV PATH=/root/.claude/bin:$PATH

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["entrypoint.sh"]
