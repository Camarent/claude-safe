ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: local-cluster dev: docker (own daemon), act, kubectl, kind, helm, Slurm (one node) + munge; start with --with cluster

# Docker engine + CLI from Debian (docker.io), iptables for its networking. The daemon only runs with
# `run.sh --with cluster` (privileged container, own dockerd; the host Docker is not reachable).
# Slurm: slurm-wlm (slurmctld, slurmd, sbatch, srun, squeue) + munge; the entrypoint writes a one-node config.
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker.io docker-cli iptables slurm-wlm munge \
    && rm -rf /var/lib/apt/lists/*

# kubectl, kind, helm, act: pinned 2026-11-05 (tag format as the upstream tag; empty ARG = latest). Prebuilt binaries, amd64/arm64.
ARG KUBECTL_VERSION=v1.37.2
ARG KIND_VERSION=v0.33.0
ARG HELM_VERSION=v4.3.2
ARG ACT_VERSION=v0.2.98
RUN arch="$(dpkg --print-architecture)" \
    && case "$arch" in amd64) actArch=x86_64 ;; arm64) actArch=arm64 ;; *) echo "unsupported arch $arch" >&2; exit 1 ;; esac \
    && latest() { curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$1/releases/latest" | sed 's|.*/tag/||'; } \
    && kubectlV="${KUBECTL_VERSION:-$(curl -fsSL https://dl.k8s.io/release/stable.txt)}" \
    && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${kubectlV}/bin/linux/${arch}/kubectl" \
    && kindV="${KIND_VERSION:-$(latest kubernetes-sigs/kind)}" \
    && curl -fsSL -o /usr/local/bin/kind "https://github.com/kubernetes-sigs/kind/releases/download/${kindV}/kind-linux-${arch}" \
    && helmV="${HELM_VERSION:-$(curl -fsSL https://get.helm.sh/helm-latest-version)}" \
    && curl -fsSL "https://get.helm.sh/helm-${helmV}-linux-${arch}.tar.gz" | tar -xz -C /tmp "linux-${arch}/helm" \
    && mv "/tmp/linux-${arch}/helm" /usr/local/bin/helm \
    && actV="${ACT_VERSION:-$(latest nektos/act)}" \
    && curl -fsSL "https://github.com/nektos/act/releases/download/${actV}/act_Linux_${actArch}.tar.gz" | tar -xz -C /usr/local/bin act \
    && chmod 755 /usr/local/bin/kubectl /usr/local/bin/kind /usr/local/bin/helm /usr/local/bin/act \
    && kubectl version --client && kind version && helm version --short && act --version \
    && docker --version && slurmctld -V && /usr/sbin/munged --version | head -1
