ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: g++, clang, clangd (language server), clang-tidy, cppcheck, cmake, make, ninja, bear (compile_commands.json)

# C++ compilers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ \
    clang \
    clangd \
    clang-tidy=1:19.0-63 \
    cppcheck=2.17.1-2 \
    bear \
    cmake \
    make \
    ninja-build \
    libclang-rt-dev \
    && rm -rf /var/lib/apt/lists/*
