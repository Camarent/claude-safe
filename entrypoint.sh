#!/usr/bin/env bash
set -uo pipefail

# Root phase: only what needs root (claude update, X socket dir, ownership). Then re-exec as the
# unprivileged `node` user, remapped to the host uid/gid so files in the mounts are never root-owned.
# HOME stays /root (every tool and rule refers to /root/.claude); it is owned by node, not by root.
if [[ "$(id -u)" == "0" ]]; then
    hostUid="${HOST_UID:-1000}"; hostGid="${HOST_GID:-1000}"
    echo -e "\033[36mChecking for Claude Code updates...\033[0m"
    if npm install -g @anthropic-ai/claude-code@latest --silent >/tmp/claude-update.log 2>&1; then
        echo -e "\033[32mClaude Code is up to date ($(claude --version 2>/dev/null || echo unknown)).\033[0m"
    else
        echo -e "\033[33mCould not check for updates (offline?) — using existing version.\033[0m"
    fi
    groupmod -o -g "$hostGid" node 2>/dev/null
    usermod -o -u "$hostUid" -g "$hostGid" -d /root node 2>/dev/null
    # Vetted apt rows (EXTRA-TOOLS.md, ACCEPT, pinned) are installed here, the only place with root.
    # A row accepted mid-session is installed at the next container start.
    [[ -r /root/.claude/tools/xtools.py ]] && python3 -I /root/.claude/tools/xtools.py check --install --kinds apt >/tmp/xtools-apt.log 2>&1
    mkdir -p -m 1777 /tmp/.X11-unix
    : > /run/strata-phase; chown "$hostUid:$hostGid" /run/strata-phase   # Strata's entrypoint reports its phase here and cannot write to root-owned /run
    mkdir -p /opt/jstools /data
    # Hand over what an earlier root session left behind (host files become the host user's again).
    # Only root-owned entries are touched; -xdev keeps this to one filesystem per mount.
    for d in /root /root/.claude /root/.cache/chrome-devtools-mcp/chrome-profile /data /opt/jstools /opt/cargo /opt/rustup /workspace; do
        [[ -d "$d" ]] && find "$d" -xdev -user root -exec chown -h "$hostUid:$hostGid" {} + 2>/dev/null
    done
    [[ -f /root/.claude.json ]] && chown "$hostUid:$hostGid" /root/.claude.json
    # --cluster: own dockerd (the container is privileged) and a one-node Slurm. Both need root; logs in /tmp.
    if [[ "${CLUSTER:-0}" == "1" ]]; then
        # cgroup v2 in a privileged container: move our processes to a leaf so dockerd/kind can manage the tree.
        if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
            mkdir -p /sys/fs/cgroup/init
            for pid in $(cat /sys/fs/cgroup/cgroup.procs); do echo "$pid" > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || [[ ! -d /proc/$pid ]] || echo -e "\033[33mcgroup: could not move pid $pid to /sys/fs/cgroup/init\033[0m"; done
            sed -e 's/ *\([^ ]\+\)/ +\1/g' -e 's/^ //' /sys/fs/cgroup/cgroup.controllers > /sys/fs/cgroup/cgroup.subtree_control \
                || echo -e "\033[33mcgroup: could not enable controllers in subtree_control (dockerd/kind may fail)\033[0m"
        else
            echo -e "\033[33mcgroup v2 not found: dockerd/kind may fail on a cgroup v1 host.\033[0m"
        fi
        dockerd >/tmp/dockerd.log 2>&1 &
        for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 0.5; done
        if docker info >/dev/null 2>&1; then
            chgrp "$hostGid" /var/run/docker.sock && chmod 660 /var/run/docker.sock
            if setpriv --reuid=node --regid=node --init-groups docker info >/dev/null 2>&1; then
                echo -e "\033[32mDocker daemon ready (own dockerd, logs /tmp/dockerd.log).\033[0m"
            else
                echo -e "\033[33mDocker daemon runs but user node cannot use the socket (/var/run/docker.sock).\033[0m"
            fi
        else
            echo -e "\033[33mDocker daemon did not start (see /tmp/dockerd.log).\033[0m"
        fi
        if command -v slurmctld >/dev/null; then
            mkdir -p /var/spool/slurmd /var/spool/slurmctld /var/log/slurm /run/munge /etc/slurm
            [[ -s /etc/munge/munge.key ]] || { mungekey --create >/dev/null 2>&1 || dd if=/dev/urandom of=/etc/munge/munge.key bs=1 count=1024 2>/dev/null; }
            chown -R munge:munge /etc/munge /var/lib/munge /var/log/munge /run/munge 2>/dev/null; chmod 400 /etc/munge/munge.key
            su -s /bin/sh munge -c munged
            nodeLine=$(slurmd -C 2>/dev/null | sed -n 's/^\(NodeName=[^ ]*\) .*\(CPUs=.*RealMemory=[0-9]*\).*/\1 \2/p' | head -n1)   # detected topology, so the node is never INVAL
            cat > /etc/slurm/slurm.conf <<SLURM
ClusterName=local
SlurmctldHost=$(hostname)
MpiDefault=none
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
ReturnToService=2
SlurmctldPidFile=/run/slurmctld.pid
SlurmdPidFile=/run/slurmd.pid
SlurmdSpoolDir=/var/spool/slurmd
StateSaveLocation=/var/spool/slurmctld
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmdLogFile=/var/log/slurm/slurmd.log
SchedulerType=sched/backfill
SelectType=select/cons_tres
SelectTypeParameters=CR_Core
$nodeLine State=UNKNOWN
PartitionName=main Nodes=ALL Default=YES MaxTime=INFINITE State=UP
SLURM
            printf 'CgroupPlugin=disabled\nIgnoreSystemd=yes\n' > /etc/slurm/cgroup.conf   # no systemd in the container
            slurmctld >/tmp/slurmctld.out 2>&1; slurmd >/tmp/slurmd.out 2>&1
            # Wait for the node to register and turn idle; anything else is reported with the reason, never hidden.
            slurmState=""
            for _ in $(seq 1 30); do slurmState=$(sinfo -h -o %t 2>/dev/null | head -n1); [[ "$slurmState" == "idle" ]] && break; sleep 0.5; done
            chmod -R a+rX /var/log/slurm; chmod a+r /var/log/slurm/*.log 2>/dev/null
            if [[ "$slurmState" == "idle" ]]; then
                echo -e "\033[32mSlurm one-node cluster ready (sinfo, sbatch; logs /var/log/slurm).\033[0m"
            else
                echo -e "\033[33mSlurm NOT ready (node state '${slurmState:-none}', nodeLine '${nodeLine:-empty}'). See /tmp/slurmctld.out, /tmp/slurmd.out, /var/log/slurm; sinfo -R.\033[0m"
            fi
        fi
    fi
    exec setpriv --reuid=node --regid=node --init-groups env HOME=/root USER=node "$0" "$@"
fi

# Command index check: every hook command in settings.json and every cs-* name in paths.json must resolve on PATH.
# Warns loudly; the exit code is non-zero only when CS_STRICT=1.
if [[ -r /root/.claude/tools/cmdindex.py && -r /root/.claude/settings.json ]]; then
    python3 -I /root/.claude/tools/cmdindex.py check-path --settings /root/.claude/settings.json || exit 1
fi

# Virtual display for the browser. Chrome runs headful against this rather than
# --headless, which is trivially fingerprinted by bot detection.
export DISPLAY=:99
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &

for _ in $(seq 1 50); do
    [[ -e "/tmp/.X11-unix/X${DISPLAY#:}" ]] && break
    sleep 0.1
done

if [[ -e "/tmp/.X11-unix/X${DISPLAY#:}" ]]; then
    echo -e "\033[32mVirtual display ready on $DISPLAY — browser tools available.\033[0m"
else
    echo -e "\033[33mXvfb did not start (see /tmp/xvfb.log) — browser tools may fail.\033[0m"
fi

chromeProfile=/root/.cache/chrome-devtools-mcp/chrome-profile
mkdir -p "$chromeProfile"

# The profile outlives the container, and so do Chrome's singleton locks if a
# session ends uncleanly. They point at the old container's hostname, so Chrome
# then refuses to start ("browser is already running") for every later session.
# Only one container uses this profile at a time, so clearing them is safe.
rm -f "$chromeProfile"/Singleton{Lock,Socket,Cookie}

# snip installs itself as a PreToolUse hook on Bash in ~/.claude/settings.json,
# and unpacks its filter library into ~/.config/snip. The settings file is
# bind-mounted from the host and the filters are not, so this runs every start.
# It is idempotent: repeated runs leave a single hook entry.
if [[ "${SNIP_OFF:-0}" == "1" ]]; then
    snip init --uninstall >/dev/null 2>&1
    echo -e "\033[33msnip disabled — shell output reaches Claude unfiltered.\033[0m"
elif snip init >/dev/null 2>&1; then
    echo -e "\033[32msnip active — verbose shell output is filtered ($(snip gain 2>/dev/null | grep -oE '[0-9.]+[KM]? tokens saved' | head -1 || echo 'no history yet')).\033[0m"
else
    echo -e "\033[33mCould not install the snip hook — continuing without it.\033[0m"
fi

# ponytail (opt-in via --ponytail) is a plugin stored in the bind-mounted
# ~/.claude, so it would persist into later sessions. Enable it only when asked
# and disable it otherwise.
if [[ "${PONYTAIL:-0}" == "1" ]]; then
    if claude plugin marketplace add DietrichGebert/ponytail >/dev/null 2>&1 \
        && { claude plugin install ponytail@ponytail >/dev/null 2>&1 || claude plugin enable ponytail@ponytail >/dev/null 2>&1; }; then
        echo -e "\033[32mponytail enabled — Claude is biased toward minimal code.\033[0m"
    else
        echo -e "\033[33mCould not install ponytail — continuing without it.\033[0m"
    fi
else
    claude plugin disable ponytail@ponytail >/dev/null 2>&1 || true
fi

# Strata is never started here: the pipeline starts it when it needs it (search/strata.py preflight -> `modelctl strata start`),
# and `modelctl use ollama|strata|none` switches the GPU by hand.
if [[ "${STRATA_SIDE:-0}" == "1" && ! -x /opt/strata/entrypoint.sh ]]; then
    echo -e "\033[33mStrata is not in this image; /run-stage will have no local lead.\033[0m"
fi

if [[ -n "${RUN_CMD:-}" ]]; then exec bash -c "$RUN_CMD"; fi

exec claude --mcp-config /etc/claude/mcp-servers.json "$@"
