ARG BASE=claude-safe:latest
# Pinned by digest (ollama/ollama:latest, client 0.40.16, resolved 2026-11-13). To upgrade: pick a tag, then
# `docker buildx imagetools inspect ollama/ollama:<tag>` for its digest.
ARG OLLAMA_IMAGE=ollama/ollama@sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592
FROM ${OLLAMA_IMAGE} AS ollama
FROM ${BASE}
# desc: Ollama server (CUDA 13 libs only), started and stopped with `modelctl`; models in /ollama-data, GGUF files from ~/kata-models at /models; added by --with ollama (implies gpu)

# Binary and libraries are copied from Ollama's own image, never compiled. Left out: cuda_v12, mlx_cuda_v13, vulkan, headers
# (about 3.5 GB). The image is Ubuntu 24.04 (glibc 2.39), this base is Debian 13 (glibc 2.41), so it runs as is.
COPY --from=ollama /usr/bin/ollama /usr/local/bin/ollama
COPY --from=ollama /usr/lib/ollama /opt/ollama-full
RUN mkdir -p /usr/lib/ollama \
    && cd /opt/ollama-full \
    && cp -a *.so* llama-server llama-quantize cuda_v13 /usr/lib/ollama/ \
    && rm -rf /opt/ollama-full \
    && ollama --version 2>&1 | head -2

ENV OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/ollama-data/models \
    OLLAMA_FLASH_ATTENTION=1 \
    OLLAMA_KEEP_ALIVE=-1
