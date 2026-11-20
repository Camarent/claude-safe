#!/usr/bin/env bash
set -euo pipefail

imageName="claude-safe"
scriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dockerfilePath="$scriptDir"
claudeDockerDir="$scriptDir/.claude"
browserProfileDir="$scriptDir/browser-profile"

# Add folders you always trust here, e.g. "/home/you/projects"
trustedPaths=(
)

sensitivePaths=(
    "/"
    "/root"
    "/home"
    "/etc"
    "/usr"
    "$HOME"
    "$HOME/Documents"
    "$HOME/Desktop"
)

scratchDir="$scriptDir/scratch"

# Default: mount an empty scratch dir, so an accidental bare `claude-safe`
# never exposes a real project. Pass --here to mount $PWD instead.
mountMode="scratch"
forceRebuild=false
profiles=()
snipOff=false
ponytailOn=false
gpuOn=false
noSecrets=false
claudeArgs=()
compactWindow=""
strataSide=false
clusterOn=false
ollamaOn=false
runCmd=""
publishPorts=()
strataDataDir="$scriptDir/strata-data"
usage() {
    local f p desc
    cat <<HELP
Usage: claude-safe [--here] [--with PROFILES] [--rebuild] [--compact N] [--no-snip] [--ponytail] [--no-secrets] [--help] [-- CLAUDE_ARGS...]

  --here           mount the current folder (default: empty scratch dir)
  --cmd STRING     run STRING with bash instead of the agent TUI 
                   STRATA_FAMILY / STRATA_MODEL env pick the local model (default: qwen / IQ3_XXS)
  --no-secrets     don't pass GH/Cloudflare tokens or mount the browser profile
  --                everything after is passed to claude (e.g. -- -p "prompt");
                   without a terminal the container runs non-interactively
  --with a,b       add toolchain profiles on top of the base image, plus the run features below
  --rebuild        rebuild the base image and all profile images first
  --compact N      hard auto-compact window, e.g. 150k or 200000 (100k-1M; default: auto)
  --publish PORT   publish container port PORT on the host loopback only (127.0.0.1:PORT); repeatable
  --no-snip        disable snip output filtering
  --ponytail       enable the ponytail plugin (minimal-code bias); off by default, disabled again on other runs
  -h, --help       show this help

Profiles and features (--with, comma-separated or repeated):
HELP
    for f in "$scriptDir"/profiles/*.Dockerfile; do
        p="$(basename "$f" .Dockerfile)"
        desc="$(sed -n 's/^# desc: *//p' "$f" | head -n1)"
        printf '  %-8s %s\n' "$p" "$desc"
    done
    cat <<HELP
  gpu      (feature, no image) pass all NVIDIA GPUs into the container (needs nvidia-container-toolkit)
  strata   also: normal Claude PLUS Strata beside it (started on demand by the pipeline) for tools/orch.py (/run-stage); implies gpu
  ollama   also: Ollama beside Claude, started/stopped with modelctl (data in ollama-data/, GGUF from \$KATA_MODELS or ~/kata-models at /models); implies gpu
  cluster  also: PRIVILEGED container with its own dockerd and one-node Slurm (Docker-in-Docker; images in volume claude-safe-docker)

Examples:
  claude-safe --here --with rust
  claude-safe --here --with rust,cpp
  claude-safe --here --with strata,cluster
HELP
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --here)    mountMode="here"; shift ;;
        --rebuild) forceRebuild=true; shift ;;
        --cmd)
            [[ $# -ge 2 ]] || { echo -e "\033[31m--cmd needs a command string\033[0m"; exit 1; }
            runCmd="$2"; shift 2 ;;
        --with)
            [[ $# -ge 2 ]] || { echo -e "\033[31m--with needs a list, e.g. --with rust,cpp\033[0m"; exit 1; }
            IFS=',' read -r -a _w <<< "$2"; profiles+=("${_w[@]}"); shift 2 ;;
        --compact)
            [[ $# -ge 2 ]] || { echo -e "\033[31m--compact needs a size, e.g. --compact 150k\033[0m"; exit 1; }
            compactWindow="$2"; shift 2 ;;
        --publish)
            [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || { echo -e "\033[31m--publish needs a port number\033[0m"; exit 1; }
            publishPorts+=(-p "127.0.0.1:$2:$2"); shift 2 ;;
        --no-snip) snipOff=true; shift ;;
        --ponytail) ponytailOn=true; shift ;;
        --no-secrets) noSecrets=true; shift ;;
        --) shift; claudeArgs=("$@"); break ;;
        *)
            echo -e "\033[31mUnknown argument '$1'.\033[0m"; usage; exit 1
            ;;
    esac
done

# Run features chosen through --with: `gpu` has no image, `strata` and `cluster` have one and also change how the container runs.
imageProfiles=()
for p in "${profiles[@]}"; do
    case "$p" in
        gpu) gpuOn=true ;;
        strata) strataSide=true; gpuOn=true; imageProfiles+=("$p") ;;
        ollama) gpuOn=true; ollamaOn=true; imageProfiles+=("$p") ;;
        cluster) clusterOn=true; imageProfiles+=("$p") ;;
        *) imageProfiles+=("$p") ;;
    esac
done
profiles=("${imageProfiles[@]}")

if [[ "$mountMode" == "here" ]]; then
    currentPath="$(pwd)"

    isTrusted=false
    for p in "${trustedPaths[@]}"; do
        [[ -z "$p" ]] && continue
        if [[ "$currentPath" == "$p" || "$currentPath" == "$p"/* ]]; then
            isTrusted=true
            break
        fi
    done

    isSensitive=false
    if [[ "$isTrusted" == false ]]; then
        for p in "${sensitivePaths[@]}"; do
            if [[ "$currentPath" == "$p" ]]; then
                isSensitive=true
                break
            fi
        done
        # Also refuse any top-level folder directly under $HOME (e.g. ~/workspace,
        # ~/Downloads) — forces you to cd into an actual project subfolder.
        if [[ "$(dirname "$currentPath")" == "$HOME" ]]; then
            isSensitive=true
        fi
    fi

    if [[ "$isSensitive" == true ]]; then
        echo -e "\033[31mRefusing to mount '$currentPath' — navigate to a project folder first.\033[0m"
        exit 1
    fi

    mountPath="$currentPath"
    echo -e "\033[33mMounting current folder: $mountPath\033[0m"
else
    mkdir -p "$scratchDir"
    mountPath="$scratchDir"
    echo -e "\033[33mNo folder mounted (scratch dir only) — run 'claude-safe --here' to mount $(pwd).\033[0m"
fi

if ! docker info >/dev/null 2>&1; then
    echo -e "\033[31mDocker is not responding. Start the engine: sudo systemctl start docker\033[0m"
    exit 1
fi

# Image = base + optional toolchain layers, chained one profile at a time
# (claude-safe -> claude-safe:cpp -> claude-safe:cpp-rust). Profiles are sorted
# and de-duplicated so --with rust,cpp and --with cpp,rust share one image, and
# the shared prefix layers are reused between combinations.
declare -A builtTags=()
buildImage() { # tag, dockerfile, extra docker build args...
    local tag="$1" file="$2"; shift 2
    [[ -n "${builtTags[$tag]:-}" ]] && return 0
    # --no-cache so unpinned "latest" lookups actually re-run. --pull only for
    # the base image: profile bases are local tags, not in any registry.
    local cacheArgs=()
    if [[ "$forceRebuild" == true ]]; then
        cacheArgs=(--no-cache)
        [[ "$tag" == "claude-safe" ]] && cacheArgs+=(--pull)
    fi
    if [[ "$forceRebuild" == true ]] || [[ -z "$(docker images -q "$tag" 2>/dev/null)" ]]; then
        echo -e "\033[36mBuilding $tag...\033[0m"
        docker build "${cacheArgs[@]}" -t "$tag" -f "$file" "$@" "$dockerfilePath"
        builtTags[$tag]=1
    fi
}

# Build the image for a sorted, de-duplicated profile list, one layer per
# profile; sets $chainImage to the final tag.
buildChain() { # profile names...
    local p prev chain="" list=()
    # Order: sorted, with each profile's "# requires: a b" lines (transitively)
    # placed before it, so every caller (--with, --rebuild's per-profile and
    # combination passes) gets them built first.
    addProfile() { # name
        local n="$1" reqFile r
        [[ " ${list[*]} " == *" $n "* ]] && return 0
        reqFile="$dockerfilePath/profiles/$n.Dockerfile"
        if [[ -f "$reqFile" ]]; then
            for r in $(sed -n 's/^# requires: *//p' "$reqFile" | head -n1); do
                addProfile "$r"
            done
        fi
        list+=("$n")
    }
    while read -r p; do addProfile "$p"; done < <(printf '%s\n' "$@" | sort -u)
    for p in "${list[@]}"; do
        if [[ ! -f "$dockerfilePath/profiles/$p.Dockerfile" ]]; then
            echo -e "\033[31mUnknown profile '$p'. Available: $(cd "$dockerfilePath/profiles" && ls *.Dockerfile | sed 's/\.Dockerfile//' | paste -sd, -)\033[0m"
            exit 1
        fi
        prev="claude-safe${chain:+:$chain}"
        chain="${chain:+$chain-}$p"
        buildImage "claude-safe:$chain" "$dockerfilePath/profiles/$p.Dockerfile" --build-arg "BASE=$prev"
    done
    chainImage="claude-safe${chain:+:$chain}"
}

buildImage "$imageName" "$dockerfilePath/Dockerfile"

# --rebuild refreshes every profile image, not just the requested ones: each
# available profile on its own, plus any combination image built earlier.
if [[ "$forceRebuild" == true ]]; then
    for f in "$dockerfilePath"/profiles/*.Dockerfile; do
        buildChain "$(basename "$f" .Dockerfile)"
    done
    while read -r tag; do
        [[ "$tag" == *-* ]] || continue
        IFS='-' read -r -a _c <<< "$tag"
        buildChain "${_c[@]}"
    done < <(docker images --format '{{.Tag}}' claude-safe | grep -v '^<none>$' | awk '{print gsub(/-/,"-") " " $0}' | sort -n | cut -d' ' -f2)
fi

if [[ ${#profiles[@]} -gt 0 ]]; then
    mapfile -t profiles < <(printf '%s\n' "${profiles[@]}" | sort -u)
    buildChain "${profiles[@]}"
    imageName="$chainImage"
fi

# Ensure container's own .claude dir exists — Claude manages everything inside it
mkdir -p "$claudeDockerDir"

# Persist the browser profile across runs: cookies, logins and dismissed consent
# banners survive --rm, so sites see a returning visitor rather than a fresh one.
# The path below is chrome-devtools-mcp's own default profile location — it has
# no flag to relocate it, so the mount has to land exactly there.
mkdir -p "$browserProfileDir"

# Restore .claude.json from latest backup if missing (Claude stores auth/theme state here)
if [[ ! -f "$claudeDockerDir/.claude.json" ]]; then
    latestBackup="$(ls -t "$claudeDockerDir/backups" 2>/dev/null | head -n1 || true)"
    if [[ -n "$latestBackup" ]]; then
        cp "$claudeDockerDir/backups/$latestBackup" "$claudeDockerDir/.claude.json"
    else
        # No backup either — seed an empty file so the bind mount below
        # attaches a file, not a directory.
        echo '{}' > "$claudeDockerDir/.claude.json"
    fi
fi

# Each run gets its own container, so several sessions can run side by side
# (one per terminal tab) without the newest one killing the others.
sessionName="claude-safe-$(date +%Y%m%d-%H%M%S)-$$"

# Chrome locks its profile to a single running browser, and the entrypoint
# clears stale locks on start — so two sessions on the shared profile would
# trample each other. Only the first concurrent session gets the persistent
# profile; any extra ones use a throwaway profile inside their own container.
browserProfileArgs=()
if [[ -z "$(docker ps -q --filter label=claude-safe.browser-profile=shared)" ]]; then
    browserProfileArgs=(
        --label claude-safe.browser-profile=shared
        -v "$browserProfileDir:/root/.cache/chrome-devtools-mcp/chrome-profile"
    )
else
    echo -e "\033[33mAnother session holds the browser profile — this one uses a temporary profile (no saved logins).\033[0m"
fi

# Pass a GitHub token through for the session only (gh reads GH_TOKEN natively,
# no `gh auth login` or on-disk config needed) — nothing persists after --rm.
# Falls back to the host's own `gh` login so you don't have to set GH_TOKEN by hand.
ghToken="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$ghToken" ]] && command -v gh >/dev/null 2>&1; then
    ghToken="$(gh auth token 2>/dev/null || true)"
fi

ghTokenArgs=()
if [[ -n "$ghToken" ]]; then
    export GH_TOKEN="$ghToken"  # docker reads the value from this env; "-e NAME=value" would show it in ps and docker inspect
    ghTokenArgs=(-e GH_TOKEN)
fi

# Cloudflare API token (wrangler and the API read CLOUDFLARE_API_TOKEN natively).
# Taken from the host environment, else from a file kept outside this repo.
cfToken="${CLOUDFLARE_API_TOKEN:-}"
cfTokenFile="$HOME/.config/claude-safe/cloudflare-token"
if [[ -z "$cfToken" && -f "$cfTokenFile" ]]; then
    cfToken="$(<"$cfTokenFile")"
fi

cfTokenArgs=()
if [[ -n "$cfToken" ]]; then
    export CLOUDFLARE_API_TOKEN="$cfToken"
    cfTokenArgs=(-e CLOUDFLARE_API_TOKEN)
fi

# Second Claude account for the pipeline's headless `claude -p` sessions only
# (pipeline/claude.py turns it into CLAUDE_CODE_OAUTH_TOKEN per session). The
# interactive session keeps its own login. Token comes from `claude setup-token`
# run as that account; kept outside this repo.
plTokenFile="$HOME/.config/claude-safe/pipeline-claude-token"
plToken="${PIPELINE_CLAUDE_TOKEN:-}"
if [[ -z "$plToken" && -f "$plTokenFile" ]]; then
    plToken="$(<"$plTokenFile")"
fi

plTokenArgs=()
if [[ -n "$plToken" ]]; then
    export PIPELINE_CLAUDE_TOKEN="$plToken"  # docker reads the value from this env; "-e NAME=value" would show it in ps and docker inspect
    plTokenArgs=(-e PIPELINE_CLAUDE_TOKEN)
fi

# Unattended runs: no tokens, no logged-in browser profile.
if [[ "$noSecrets" == true ]]; then
    ghTokenArgs=()
    cfTokenArgs=()
    plTokenArgs=()
    browserProfileArgs=()
fi

# Escape hatch: snip rewrites and filters the output of every shell command, so
# keep a one-flag way to take it back out of the loop when debugging.
snipArgs=()
if [[ "$snipOff" == true ]]; then
    snipArgs=(-e "SNIP_OFF=1")
fi

# ponytail lives in the bind-mounted ~/.claude, so the entrypoint must know
# every run whether to enable or disable it.
ponytailArgs=(-e "PONYTAIL=$([[ "$ponytailOn" == true ]] && echo 1 || echo 0)")

# Hard auto-compact window for this session. The env var takes precedence over
# any autoCompactWindow in settings.json; claude validates the value itself.
compactArgs=()
if [[ -n "$compactWindow" ]]; then
    compactArgs=(-e "CLAUDE_CODE_AUTO_COMPACT_WINDOW=${compactWindow}")
fi

gpuArgs=()
if [[ "$gpuOn" == true ]]; then
    gpuArgs=(--gpus all)
fi

# cluster: the container runs its own dockerd (docker-in-docker), which needs --privileged. The host
# Docker socket is NOT mounted. /var/lib/docker is a named volume so kind/act images survive restarts.
clusterArgs=()
if [[ "$clusterOn" == true ]]; then
    clusterArgs=(--privileged -e CLUSTER=1 -v claude-safe-docker:/var/lib/docker)
    echo -e "\033[33mcluster: container is privileged (own Docker + Slurm inside).\033[0m"
fi

# Per-mode mounts. Strata's port is never published; Claude reaches it inside the container.
# memlock/shm are what Strata's own docs ask for.
shmSize="1g"
homeArgs=(-v "$claudeDockerDir:/root/.claude" -v "$claudeDockerDir/.claude.json:/root/.claude.json")
if [[ "$strataSide" == true ]]; then
    # Claude stays the cloud session. Strata runs beside it; orch.py calls it for the local lead and the
    # verdict judge, with the key shared through STRATA_KEY (never given to Claude as its own credential).
    mkdir -p "$strataDataDir"
    shmSize="2g"
    strataKey="$(openssl rand -hex 24)"
    availGb=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
    if [[ "${STRATA_MODEL:-IQ3_XXS}" == "IQ3_XXS" && "$availGb" -lt 50 ]]; then
        echo -e "\033[33mOnly ${availGb} GB RAM available; IQ3_XXS wants about 50 GB. Close big apps.\033[0m"
    fi
    homeArgs+=(-v "$strataDataDir:/data" --ulimit memlock=-1:-1 --stop-timeout 120
               -e STRATA_SIDE=1 -e "API_KEY=$strataKey" -e "STRATA_KEY=$strataKey" -e ORCH_LEAD=1
               -e "FAMILY=${STRATA_FAMILY:-qwen}" -e "MODEL=${STRATA_MODEL:-IQ3_XXS}"
               -e "CONTEXT=${STRATA_CONTEXT:-128k}" -e "VISION=${STRATA_VISION:-off}" -e "PARALLEL=${STRATA_PARALLEL:-}"
               # 12 GB NVIDIA cards answer faster with the STRATA_ROUTE_PRIOR routing (0.2.8 notes); it is off
               # in the engine by default because it was tied to repeated text in long sessions, which
               # the strata feature does not run.
               -e "STRATA_SET=STRATA_ROUTE_PRIOR=${STRATA_ROUTE_PRIOR:-1.5} vram-reserve-mib=${STRATA_VRAM_RESERVE_MIB:-832}")
fi
# Local model servers are switched inside the container with `modelctl` (scripts/modelctl): one engine on the GPU at a time.
# Strata is not started at boot: the pipeline starts it when it needs it (modelctl strata start).
homeArgs+=(-v "$scriptDir/scripts/modelctl:/usr/local/bin/modelctl:ro")
if [[ "$ollamaOn" == true ]]; then
    ollamaDataDir="$scriptDir/ollama-data"; mkdir -p "$ollamaDataDir"
    homeArgs+=(-v "$ollamaDataDir:/ollama-data")
    modelsDir="${KATA_MODELS:-$HOME/kata-models}"
    [[ -d "$modelsDir" ]] && homeArgs+=(-v "$modelsDir:/models:ro")
fi
echo -e "\033[32mStarting sandboxed Claude...\033[0m"

ttyArgs=(-i)
if [[ -t 0 && -t 1 ]]; then
    ttyArgs=(-it)
fi

docker run "${ttyArgs[@]}" --rm --init \
    --name "$sessionName" \
    --label claude-safe=1 \
    --shm-size="$shmSize" \
    -e HOME=/root \
    -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)" \
    -e "RUN_CMD=$runCmd" \
    "${ghTokenArgs[@]}" \
    "${cfTokenArgs[@]}" \
    "${plTokenArgs[@]}" \
    "${snipArgs[@]}" \
    "${ponytailArgs[@]}" \
    "${compactArgs[@]}" \
    "${gpuArgs[@]}" \
    "${publishPorts[@]}" \
    "${clusterArgs[@]}" \
    "${homeArgs[@]}" \
    "${browserProfileArgs[@]}" \
    -v "$scriptDir/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro" \
    -v "$mountPath:/workspace" \
    -w /workspace \
    "$imageName" \
    "${claudeArgs[@]}"
