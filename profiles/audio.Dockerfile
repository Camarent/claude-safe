ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: ffmpeg, yt-dlp, faster-whisper (pulls in python)

# ffmpeg (incl. ffprobe) for trim / loudness / MP3 encode. python3-venv and pip
# come from the base image.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp + faster-whisper in a shared venv under /opt (works regardless of $HOME).
# yt-dlp is on PATH; use faster-whisper via /opt/audio-venv/bin/python.
# The nvidia-* wheels are the CUDA 12 runtime libs faster-whisper needs on a GPU
# (run the container with `claude-safe --with gpu`); they sit unused on CPU-only runs.
# Whisper models are NOT baked in: they download on first use (`small` is ~0.5 GB),
# so point the model cache at a mounted project folder to keep them across runs.
# Pinned (2026-11-05); set an ARG empty for newest.
ARG YT_DLP_VERSION=2026.10.26
ARG FASTER_WHISPER_VERSION=1.2.1
RUN python3 -m venv /opt/audio-venv \
    && /opt/audio-venv/bin/pip install --no-cache-dir \
        "yt-dlp${YT_DLP_VERSION:+==$YT_DLP_VERSION}" \
        "faster-whisper${FASTER_WHISPER_VERSION:+==$FASTER_WHISPER_VERSION}" \
    && /opt/audio-venv/bin/pip install --no-cache-dir nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" \
    && ln -sf /opt/audio-venv/bin/yt-dlp /usr/local/bin/yt-dlp \
    && yt-dlp --version \
    && /opt/audio-venv/bin/python -c "import faster_whisper"

# ctranslate2 finds the CUDA libs through the loader path, not through the venv.
ENV LD_LIBRARY_PATH=/opt/audio-venv/lib/python3.13/site-packages/nvidia/cublas/lib:/opt/audio-venv/lib/python3.13/site-packages/nvidia/cudnn/lib
