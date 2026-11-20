#!/usr/bin/env bash
# Provision a plain Ubuntu Server VM (normal sudo user, no GPU, no container) with the tools the
# claude-safe Docker image has, minus the PC-only profiles (strata, ollama, cluster, exttest),
# modelctl and the GPU parts. Source of truth: Dockerfile and profiles/*.Dockerfile; the pinned
# versions below are copies of their ARG lines (tests/test_provision_script.py compares them).
# Rerunnable: every step checks its result first and prints "skip <step>: already done".
#
# Checked against ubuntu:26.04 (apt, uv, gitleaks, snip, config steps only). Assumptions:
# UNVERIFIED: Docker, PGDG, GitHub CLI and Chrome repos publish a suite for this codename (checked at run time).
# UNVERIFIED: NodeSource "nodistro" suite serves NODE_MAJOR (26 here, matching the claude-safe image; raise it when the image moves).
# UNVERIFIED: Ubuntu's `chromium` apt package is a snap shim; `browser` may then point at a snap.
# UNVERIFIED: the Debian-only pins clang-tidy=1:19.0-63 and cppcheck=2.17.1-2 are dropped (distro versions).
# UNVERIFIED: the ask-venv (fastembed) and the NVIDIA wheels of the audio profile are not installed.
set -euo pipefail

# --- pins (copied from Dockerfile / profiles) -------------------------------------------------
CHROME_DEVTOOLS_MCP_VERSION=1.13.0
SNIP_VERSION=0.26.0
GITLEAKS_VERSION=8.30.1
COVERAGE_VERSION=7.17.3
PIP_AUDIT_VERSION=2.10.1
BANDIT_VERSION=1.9.4
VULTURE_VERSION=2.16
LIZARD_VERSION=1.26.1
RUFF_VERSION=0.17.5
PYTEST_VERSION=9.2.0
PYTEST_COV_VERSION=7.1.0
MYPY_VERSION=2.4.1
HUGGINGFACE_HUB_VERSION=2.4.0
PYRIGHT_VERSION=1.1.414
TYPESCRIPT_VERSION=7.0.2
KNIP_VERSION=6.45.1
JSCPD_VERSION=5.4.3
MADGE_VERSION=8.0.0
ESLINT_VERSION=10.15.0
ESBUILD_VERSION=0.28.2
WEB_EXT_VERSION=10.8.0
VSCE_VERSION=4.1.1
CLAUDE_CODE_VERSION=2.1.325
WASM_PACK_VERSION=0.15.1
CARGO_AUDIT_VERSION=0.22.2
CARGO_DENY_VERSION=0.20.2
CARGO_MACHETE_VERSION=0.9.2
CARGO_LLVM_COV_VERSION=0.9.1
CARGO_BLOAT_VERSION=0.12.1
PANDOC_VERSION=3.14
TYPST_VERSION=v0.15.1
YT_DLP_VERSION=2026.10.26
FASTER_WHISPER_VERSION=1.2.1
# Not in the Dockerfile (it uses the node:lts image); see UNVERIFIED above.
NODE_MAJOR=26
PG_MAJOR=17

# name|description (run order). Step function: step_<name with "-" as "_">; its check: check_<same>. (The prefix keeps functions from shadowing real commands such as uv or gh.)
STEPS=(
  "base|apt base packages (git, ripgrep, jq, xvfb, python3, ctags, fonts, build-essential, cmake, gdb, tmux...)"
  "gh|GitHub CLI from the official apt repo"
  "chrome|Google Chrome (amd64) or chromium, linked as /usr/local/bin/browser"
  "node|Node.js LTS from NodeSource"
  "uv|uv (Python package manager) in /usr/local/bin"
  "pytools|Python QA tools via uv in /opt/uv-tools (coverage, pip-audit, bandit, vulture, lizard, ruff, pytest, mypy, hf)"
  "jstools|knip jscpd madge eslint in /opt/jstools; pyright, typescript, esbuild, web-ext, vsce global"
  "gitleaks|gitleaks release binary"
  "snip|snip release binary"
  "cdt-mcp|chrome-devtools-mcp (npm global, pinned)"
  "docker|Docker Engine from Docker's apt repo, user added to the docker group"
  "cpp|g++, clang, clangd, clang-tidy, cppcheck, bear, cmake, ninja"
  "rust|rustup stable, clippy, rust-analyzer, wasm32, wasm-pack, cargo audit/deny/machete/llvm-cov/bloat"
  "office|LibreOffice, pandoc, typst, tesseract (eng+fra), poppler"
  "audio|ffmpeg, yt-dlp and faster-whisper in /opt/audio-venv (CPU only)"
  "postgres|PostgreSQL 17 from PGDG plus pgvector and /opt/pg-venv (service NOT started)"
  "claude|Claude Code (npm global, pinned)"
  "config|~/.config/secrets.env, ~/.profile hook, git include of privacy.gitconfig"
)

DRY=0
ONLY=""
SKIP=""
FAILED=()
NDONE=0
NSKIP=0
ARCH="$(dpkg --print-architecture 2>/dev/null || echo unknown)"
# shellcheck disable=SC1091 # /etc/os-release is a system file
CODENAME="$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-}")"

# --- helpers ----------------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }
say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 2; }
sd() { sudo "$@"; }

pkgs_installed() {
  local p
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || return 1; done
}

apt_install() {
  sd env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
}

# add_apt_repo NAME KEY_URL BASE_URL SUITE COMPONENTS [ARCH]: signed-by keyring + sources list.
# Fails (no fallback to another suite) when BASE_URL/dists/SUITE does not exist.
add_apt_repo() {
  local name="$1" key_url="$2" base="$3" suite="$4" comps="$5" arch="${6:-$ARCH}"
  local list="/etc/apt/sources.list.d/$name.list" key="/etc/apt/keyrings/$name.gpg" tmp
  [ -f "$list" ] && return 0
  if ! curl -fsSI "$base/dists/$suite/InRelease" >/dev/null 2>&1 \
    && ! curl -fsSI "$base/dists/$suite/Release" >/dev/null 2>&1; then
    say "error: repo $base has no suite '$suite' for this OS (codename '$CODENAME'); not using another suite" >&2
    return 1
  fi
  tmp="$(mktemp)"
  curl -fsSL "$key_url" -o "$tmp"
  sd mkdir -p -m 755 /etc/apt/keyrings
  if grep -q 'BEGIN PGP' "$tmp"; then gpg --dearmor <"$tmp" | sd tee "$key" >/dev/null
  else sd cp "$tmp" "$key"; fi
  rm -f "$tmp"
  sd chmod go+r "$key"
  say "deb [arch=$arch signed-by=$key] $base $suite $comps" | sd tee "$list" >/dev/null
  sd apt-get update
}

# Pinned-version checks.
pkg_json_ver() { node -p "require('$1/package.json').version" 2>/dev/null || true; }
npm_g_has() { have npm && [ "$(pkg_json_ver "$(npm root -g)/$1")" = "$2" ]; }
uv_tool_has() { have uv && UV_TOOL_DIR=/opt/uv-tools uv tool list 2>/dev/null | grep -Eq "^$1 v$2( |\$)"; }
cargo_has() {
  [ -x /opt/cargo/bin/cargo ] \
    && CARGO_HOME=/opt/cargo /opt/cargo/bin/cargo install --list 2>/dev/null | grep -q "^$1 v$2:"
}

# download URL to a fresh temp dir, echo the dir; caller removes it.
fetch_dir() {
  local d
  d="$(mktemp -d)"
  curl -fsSL -o "$d/$2" "$1"
  echo "$d"
}

arch_pick() { # arch_pick TOOL AMD64_VALUE ARM64_VALUE
  case "$ARCH" in
    amd64) echo "$2" ;;
    arm64) echo "$3" ;;
    *) say "unsupported arch for $1: $ARCH" >&2; return 1 ;;
  esac
}

# --- steps: check_X returns 0 when done (no root needed), X does the work ----------------------
BASE_PKGS=(git curl ca-certificates ripgrep procps less jq gnupg zstd xvfb python3 python3-venv python3-pip
  universal-ctags fontconfig fonts-dejavu-core fonts-liberation2 fonts-noto-core fonts-noto-color-emoji
  fonts-noto-cjk build-essential cmake ninja-build gdb ccache tmux)
check_base() { pkgs_installed "${BASE_PKGS[@]}"; }
step_base() {
  sd apt-get update
  apt_install "${BASE_PKGS[@]}"
  sd fc-cache -f
}

check_gh() { have gh; }
step_gh() {
  add_apt_repo github-cli https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    https://cli.github.com/packages stable main
  apt_install gh
}

check_chrome() { have browser; }
step_chrome() {
  if [ "$ARCH" = amd64 ]; then
    add_apt_repo google-chrome https://dl.google.com/linux/linux_signing_key.pub \
      https://dl.google.com/linux/chrome/deb stable main amd64
    apt_install google-chrome-stable
    sd ln -sf /usr/bin/google-chrome-stable /usr/local/bin/browser
  else
    apt_install chromium
    sd ln -sf /usr/bin/chromium /usr/local/bin/browser
  fi
}

check_node() { have node && have npm; }
step_node() {
  add_apt_repo nodesource https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    "https://deb.nodesource.com/node_${NODE_MAJOR}.x" nodistro main
  apt_install nodejs
}

check_uv() { have uv; }
step_uv() {
  curl -LsSf https://astral.sh/uv/install.sh | sd env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
}

check_pytools() {
  uv_tool_has coverage "$COVERAGE_VERSION" && uv_tool_has pip-audit "$PIP_AUDIT_VERSION" \
    && uv_tool_has bandit "$BANDIT_VERSION" && uv_tool_has vulture "$VULTURE_VERSION" \
    && uv_tool_has lizard "$LIZARD_VERSION" && uv_tool_has ruff "$RUFF_VERSION" \
    && uv_tool_has pytest "$PYTEST_VERSION" && uv_tool_has mypy "$MYPY_VERSION" \
    && uv_tool_has huggingface-hub "$HUGGINGFACE_HUB_VERSION"
}
step_pytools() {
  sd env UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin sh -c "
    set -e
    uv tool install coverage==$COVERAGE_VERSION
    uv tool install pip-audit==$PIP_AUDIT_VERSION
    uv tool install bandit==$BANDIT_VERSION
    uv tool install vulture==$VULTURE_VERSION
    uv tool install lizard==$LIZARD_VERSION
    uv tool install ruff==$RUFF_VERSION
    uv tool install pytest==$PYTEST_VERSION --with pytest-cov==$PYTEST_COV_VERSION
    uv tool install mypy==$MYPY_VERSION
    uv tool install 'huggingface_hub[cli]==$HUGGINGFACE_HUB_VERSION'"
  sd chmod -R a+rX /opt/uv-tools
}

check_jstools() {
  [ "$(pkg_json_ver /opt/jstools/node_modules/knip)" = "$KNIP_VERSION" ] \
    && [ "$(pkg_json_ver /opt/jstools/node_modules/eslint)" = "$ESLINT_VERSION" ] \
    && [ "$(pkg_json_ver /opt/jstools/node_modules/jscpd)" = "$JSCPD_VERSION" ] \
    && [ "$(pkg_json_ver /opt/jstools/node_modules/madge)" = "$MADGE_VERSION" ] \
    && npm_g_has pyright "$PYRIGHT_VERSION" && npm_g_has typescript "$TYPESCRIPT_VERSION" \
    && npm_g_has esbuild "$ESBUILD_VERSION" && npm_g_has web-ext "$WEB_EXT_VERSION" \
    && npm_g_has @vscode/vsce "$VSCE_VERSION"
}
step_jstools() {
  sd mkdir -p /opt/jstools
  sd npm i --prefix /opt/jstools --ignore-scripts "knip@$KNIP_VERSION" "jscpd@$JSCPD_VERSION" \
    "madge@$MADGE_VERSION" "eslint@$ESLINT_VERSION"
  sd chown -R "$(id -un):$(id -gn)" /opt/jstools
  sd npm install -g "pyright@$PYRIGHT_VERSION" "typescript@$TYPESCRIPT_VERSION" \
    "esbuild@$ESBUILD_VERSION" "web-ext@$WEB_EXT_VERSION" "@vscode/vsce@$VSCE_VERSION"
}

check_gitleaks() { have gitleaks && gitleaks version 2>/dev/null | grep -qF "$GITLEAKS_VERSION"; }
step_gitleaks() {
  local a d
  a="$(arch_pick gitleaks x64 arm64)"
  d="$(mktemp -d)"
  curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${a}.tar.gz" \
    | tar -xz -C "$d" gitleaks
  sd install -m 755 "$d/gitleaks" /usr/local/bin/gitleaks
  rm -rf "$d"
}

check_snip() { have snip && snip --version 2>&1 | grep -qF "$SNIP_VERSION"; }
step_snip() {
  local a d
  a="$(arch_pick snip amd64 arm64)"
  d="$(fetch_dir "https://github.com/edouard-claude/snip/releases/download/v${SNIP_VERSION}/snip_${SNIP_VERSION}_linux_${a}.tar.gz" snip.tar.gz)"
  tar -xzf "$d/snip.tar.gz" -C "$d" snip
  sd install -m 755 "$d/snip" /usr/local/bin/snip
  rm -rf "$d"
}

check_cdt_mcp() { npm_g_has chrome-devtools-mcp "$CHROME_DEVTOOLS_MCP_VERSION"; }
step_cdt_mcp() { sd npm install -g "chrome-devtools-mcp@$CHROME_DEVTOOLS_MCP_VERSION"; }

DOCKER_PKGS=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
check_docker() { pkgs_installed "${DOCKER_PKGS[@]}" && id -nG | tr ' ' '\n' | grep -qx docker; }
step_docker() {
  add_apt_repo docker https://download.docker.com/linux/ubuntu/gpg \
    https://download.docker.com/linux/ubuntu "$CODENAME" stable
  apt_install "${DOCKER_PKGS[@]}"
  sd usermod -aG docker "$(id -un)"
  say "note: log out and in again for the docker group to apply"
}

CPP_PKGS=(g++ clang clangd clang-tidy cppcheck bear cmake make ninja-build libclang-rt-dev)
check_cpp() { pkgs_installed "${CPP_PKGS[@]}"; }
step_cpp() { sd apt-get update && apt_install "${CPP_PKGS[@]}"; }

RUST_ENV=(RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo "PATH=/opt/cargo/bin:$PATH")
check_rust() {
  cargo_has cargo-audit "$CARGO_AUDIT_VERSION" && cargo_has cargo-deny "$CARGO_DENY_VERSION" \
    && cargo_has cargo-machete "$CARGO_MACHETE_VERSION" && cargo_has cargo-llvm-cov "$CARGO_LLVM_COV_VERSION" \
    && cargo_has cargo-bloat "$CARGO_BLOAT_VERSION" \
    && /opt/cargo/bin/wasm-pack --version 2>/dev/null | grep -qF "$WASM_PACK_VERSION"
}
rust_wasm_pack() {
  if [ "$ARCH" = amd64 ]; then
    local t="wasm-pack-v${WASM_PACK_VERSION}-x86_64-unknown-linux-musl" d
    d="$(fetch_dir "https://github.com/rustwasm/wasm-pack/releases/download/v${WASM_PACK_VERSION}/$t.tar.gz" wp.tar.gz)"
    tar -xzf "$d/wp.tar.gz" -C "$d"
    install -m 755 "$d/$t/wasm-pack" /opt/cargo/bin/wasm-pack
    rm -rf "$d"
  else
    cargo install wasm-pack --version "$WASM_PACK_VERSION" --locked
  fi
}
step_rust() {
  sd apt-get update && apt_install gcc libc6-dev
  sd mkdir -p /opt/rustup /opt/cargo
  sd chown "$(id -un):$(id -gn)" /opt/rustup /opt/cargo
  # shellcheck disable=SC2016 # $PATH must stay literal in profile.d
  printf 'export RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo\nexport PATH=/opt/cargo/bin:$PATH\n' \
    | sd tee /etc/profile.d/rust.sh >/dev/null
  export "${RUST_ENV[@]}"
  [ -x /opt/cargo/bin/rustup ] || curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
  rustup component add clippy rust-analyzer llvm-tools-preview
  rustup target add wasm32-unknown-unknown
  rust_wasm_pack
  cargo install --locked "cargo-audit@$CARGO_AUDIT_VERSION" "cargo-deny@$CARGO_DENY_VERSION" \
    "cargo-machete@$CARGO_MACHETE_VERSION" "cargo-llvm-cov@$CARGO_LLVM_COV_VERSION" "cargo-bloat@$CARGO_BLOAT_VERSION"
  rm -rf /opt/cargo/registry /opt/cargo/git
}

OFFICE_PKGS=(poppler-utils xz-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra fonts-dejavu-core
  fonts-liberation2 fonts-noto-core fonts-noto-color-emoji libreoffice-writer libreoffice-calc
  libreoffice-impress libreoffice-core fonts-crosextra-carlito fonts-crosextra-caladea)
check_office() {
  pkgs_installed "${OFFICE_PKGS[@]}" && pandoc --version 2>/dev/null | sed -n 1p | grep -qF "$PANDOC_VERSION" \
    && typst --version 2>/dev/null | grep -qF "${TYPST_VERSION#v}"
}
step_office() {
  local d a="$ARCH" ta
  sd apt-get update && apt_install "${OFFICE_PKGS[@]}"
  d="$(fetch_dir "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${a}.deb" pandoc.deb)"
  sd dpkg -i "$d/pandoc.deb"
  rm -rf "$d"
  ta="$(arch_pick typst x86_64 aarch64)"
  d="$(fetch_dir "https://github.com/typst/typst/releases/download/${TYPST_VERSION}/typst-${ta}-unknown-linux-musl.tar.xz" typst.tar.xz)"
  tar -xJf "$d/typst.tar.xz" -C "$d"
  sd install -m 755 "$d/typst-${ta}-unknown-linux-musl/typst" /usr/local/bin/typst
  rm -rf "$d"
  soffice --headless --terminate_after_init >/dev/null 2>&1 || true
}

check_audio() {
  pkgs_installed ffmpeg && [ "$(/opt/audio-venv/bin/yt-dlp --version 2>/dev/null)" = "$YT_DLP_VERSION" ] \
    && /opt/audio-venv/bin/pip show faster-whisper 2>/dev/null | grep -qx "Version: $FASTER_WHISPER_VERSION"
}
step_audio() {
  sd apt-get update && apt_install ffmpeg
  sd mkdir -p /opt/audio-venv
  sd chown "$(id -un):$(id -gn)" /opt/audio-venv
  python3 -m venv /opt/audio-venv
  /opt/audio-venv/bin/pip install --no-cache-dir "yt-dlp==$YT_DLP_VERSION" "faster-whisper==$FASTER_WHISPER_VERSION"
  sd ln -sf /opt/audio-venv/bin/yt-dlp /usr/local/bin/yt-dlp
  /opt/audio-venv/bin/python -c "import faster_whisper"
}

PG_PKGS=("postgresql-$PG_MAJOR" "postgresql-client-$PG_MAJOR" "postgresql-$PG_MAJOR-pgvector")
check_postgres() { pkgs_installed "${PG_PKGS[@]}" && [ -x /opt/pg-venv/bin/python ] && have initdb; }
step_postgres() {
  local b
  sd mkdir -p /etc/postgresql-common
  printf 'create_main_cluster = false\n' | sd tee /etc/postgresql-common/createcluster.conf >/dev/null
  add_apt_repo pgdg https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    https://apt.postgresql.org/pub/repos/apt "${CODENAME}-pgdg" main
  apt_install "${PG_PKGS[@]}"
  for b in initdb pg_ctl postgres psql; do sd ln -sf "/usr/lib/postgresql/$PG_MAJOR/bin/$b" "/usr/local/bin/$b"; done
  ls "/usr/share/postgresql/$PG_MAJOR/extension/vector.control" >/dev/null
  sd mkdir -p /opt/pg-venv
  sd chown "$(id -un):$(id -gn)" /opt/pg-venv
  uv venv -q /opt/pg-venv
  uv pip install -q --python /opt/pg-venv/bin/python fastapi uvicorn sqlalchemy "psycopg[binary]" httpx
  /opt/pg-venv/bin/python -c "import fastapi, uvicorn, sqlalchemy, psycopg, httpx"
}

check_claude() { npm_g_has @anthropic-ai/claude-code "$CLAUDE_CODE_VERSION"; }
step_claude() { sd npm install -g "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"; }

# shellcheck disable=SC2088 # literal tilde on purpose: git expands it
PRIVACY_INCLUDE='~/.config/privacy.gitconfig' # literal tilde: git expands it
PROFILE_MARK="provision-dev-vm: secrets"
config_include_set() { git config --global --get-all include.path 2>/dev/null | grep -qxF "$PRIVACY_INCLUDE"; }
check_config() {
  [ -f "$HOME/.config/secrets.env" ] && grep -qF "$PROFILE_MARK" "$HOME/.profile" 2>/dev/null && config_include_set
}
step_config() {
  mkdir -p "$HOME/.config"
  chmod 700 "$HOME/.config"
  if [ ! -f "$HOME/.config/secrets.env" ]; then
    printf '%s\n' '# Secrets for this machine. KEEP chmod 600, never commit. Fill in as NAME=value lines:' \
      '# GH_TOKEN=' '# CLOUDFLARE_API_TOKEN=' '# PIPELINE_CLAUDE_TOKEN=' >"$HOME/.config/secrets.env"
  fi
  chmod 600 "$HOME/.config/secrets.env"
  if ! grep -qF "$PROFILE_MARK" "$HOME/.profile" 2>/dev/null; then
    printf '%s\n' "[ -f \"\$HOME/.config/secrets.env\" ] && { set -a; . \"\$HOME/.config/secrets.env\"; set +a; }  # $PROFILE_MARK" \
      >>"$HOME/.profile"
  fi
  if [ -n "${GIT_NAME:-}" ] && [ -n "${GIT_EMAIL:-}" ]; then
    git config --global user.name "$GIT_NAME"
    git config --global user.email "$GIT_EMAIL"
  else
    say "reminder: set git identity to the GitHub noreply address (GIT_NAME and GIT_EMAIL, or git config --global)"
  fi
  config_include_set || git config --global --add include.path "$PRIVACY_INCLUDE"
  say "reminder: create ~/.config/privacy.gitconfig from .claude/git/privacy.gitconfig.example"
}

# --- driver -----------------------------------------------------------------------------------
step_names() { local s; for s in "${STEPS[@]}"; do echo "${s%%|*}"; done; }

usage() {
  cat <<'EOF'
Usage: scripts/provision-dev-vm.sh [--only step,step] [--skip step,step] [--dry-run] [--list] [-h]
Provision an Ubuntu VM with the claude-safe toolset. Run as a normal user with sudo (not root).
  --only    run just these steps       --skip   run all but these steps
  --dry-run show what would run, change nothing, no sudo
  --list    list the steps             -h       this help
EOF
}

list_steps() { local s; for s in "${STEPS[@]}"; do printf '%-10s %s\n' "${s%%|*}" "${s#*|}"; done; }

validate_names() { # validate_names OPTION CSV
  local n
  for n in ${2//,/ }; do
    # no `step_names | grep -q`: grep exits early, SIGPIPE + pipefail then reports a valid name as unknown
    case " $(step_names | tr '\n' ' ') " in *" $n "*) ;; *) die "unknown step '$n' in $1 (see --list)" ;; esac
  done
}

selected() { # selected NAME
  local n="$1"
  if [ -n "$ONLY" ]; then case ",$ONLY," in *",$n,"*) ;; *) return 1 ;; esac; fi
  case ",$SKIP," in *",$n,"*) return 1 ;; esac
  return 0
}

run_step() { # run_step NAME DESC
  local n="$1" fn rc
  fn="${1//-/_}"
  if "check_$fn"; then
    say "skip $n: already done"; NSKIP=$((NSKIP + 1)); return 0
  fi
  if [ "$DRY" = 1 ]; then say "would run $n: $2"; return 0; fi
  say "start $n"
  set +e
  ( set -e; "step_$fn" )
  rc=$?
  set -e
  if [ "$rc" = 0 ]; then say "done $n"; NDONE=$((NDONE + 1))
  else say "FAIL $n (exit $rc)"; FAILED+=("$n"); fi
}

check_os() {
  local id ver
  # shellcheck disable=SC1091 # /etc/os-release is a system file
  read -r id ver < <(. /etc/os-release 2>/dev/null && echo "${ID:-?} ${VERSION_ID:-?}")
  [ "$id" = ubuntu ] && [ "$ver" = 26.04 ] || say "warning: written for Ubuntu 26.04, this is '${id:-?} ${ver:-?}'"
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --only) [ $# -ge 2 ] || die "--only needs a value"; ONLY="$2"; validate_names --only "$ONLY"; shift ;;
      --skip) [ $# -ge 2 ] || die "--skip needs a value"; SKIP="$2"; validate_names --skip "$SKIP"; shift ;;
      --dry-run) DRY=1 ;;
      --list) list_steps; exit 0 ;;
      -h | --help) usage; exit 0 ;;
      *) usage >&2; die "unknown argument '$1'" ;;
    esac
    shift
  done
}

main() {
  local s n
  parse_args "$@"
  [ "$(id -u)" != 0 ] || die "run as a normal user with sudo, not as root"
  [ "$DRY" = 1 ] || have sudo || die "sudo not found"
  check_os
  for s in "${STEPS[@]}"; do
    n="${s%%|*}"
    selected "$n" && run_step "$n" "${s#*|}"
  done
  say "summary: $NDONE done, $NSKIP skipped, ${#FAILED[@]} failed${FAILED[*]:+: ${FAILED[*]}}"
  [ "${#FAILED[@]}" = 0 ]
}

main "$@"
