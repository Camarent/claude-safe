ARG BASE=claude-safe:latest
# Pinned by version and digest (0.2.13 is the newest published image on 2026-11-16: 0.2.14 and 0.2.15 have no ghcr image yet, only `strata update` feeds).
# To upgrade: pick a release, then `docker buildx imagetools inspect <image>` for its digest.
ARG STRATA_IMAGE=ghcr.io/niko1221/strata:0.2.13-cuda13@sha256:b6e1876515d5663047a7cd8dfee5223c8b2d117710eb2a9c285a433fb6e4a703
FROM ${STRATA_IMAGE} AS strata
FROM ${BASE}
# desc: Strata local-LLM engine (CUDA 13, prebuilt), data in /data (run.sh mounts strata-data/); run.sh --with strata adds gpu

# Strata is taken from its own published runtime image instead of compiling it or running
# its host installer: the Terminal (bundled Python 3.12), the engine package (cudart/cublas
# included) and Strata's entrypoint come over as-is. Only the system libraries its own
# runtime image adds are installed here. Everything else lives under /opt/strata and /data.
# Inventory of what this adds: INSTALLED.md.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libatomic1 libnuma1 zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=strata /opt/strata /opt/strata
COPY --from=strata /usr/local/bin/strata /usr/local/bin/strata

# Same variables as Strata's image, except HOME: the agent keeps /root, and the
# entrypoint script is started with HOME=/data/home (see entrypoint.sh).
ENV STRATA_COMPONENTS_DIR=/opt/strata/components \
    STRATA_INSTALL_DIR=/data/install \
    XDG_STATE_HOME=/data/state \
    XDG_CONFIG_HOME=/data/config \
    PYTHONNOUSERSITE=1 \
    STRATA_IN_DOCKER=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# The vendor publishes no image after 0.2.13; the signed update feed has 0.2.14/0.2.15 (engine 0.1.60). The update runs at build
# time (no GPU needed: the engine is test-run with --version only). The feed moves, so a rebuild may pick a newer release.
RUN strata update --yes && strata --version
