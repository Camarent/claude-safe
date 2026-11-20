#!/usr/bin/env bash
# Run the pipeline test suite inside the container image (read-only mount of the current folder).
#   scripts/image_test.sh [IMAGE]     IMAGE defaults to $RELEASE_IMAGE, then claude-safe:rust
set -euo pipefail
image="${1:-${RELEASE_IMAGE:-claude-safe:rust}}"
exec docker run --rm --network none -v "$PWD":/src:ro -w /src --entrypoint python3 "$image" -I /src/.claude/tools/ptest.py
