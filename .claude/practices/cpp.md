# C++: gates, commands, traps
Practical notes for agents working in a C++ project. The project's own CLAUDE.md / AUDIT.md win over this file.

## Gates (run in this order, from the repo root)
1. Configure: `cmake -S . -B build` (add `-DCMAKE_BUILD_TYPE=Debug` and `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` so clangd and clang-tidy work).
2. Build: `cmake --build build -j`. Warnings are errors in the gate: fix them, do not silence them.
3. Test: `ctest --test-dir build --output-on-failure`. Run it through qrun: `cs-qrun -- ctest --test-dir build`.
4. Format and lint: `clang-format --dry-run --Werror <files>`, `clang-tidy -p build <files>` when the project has the config files.
5. Sanitizers (when the project has a preset): build with `-fsanitize=address,undefined` and rerun the tests.

## Commands
- One test: `ctest --test-dir build -R <name> --output-on-failure`.
- Plain Makefile projects: `make -j`, `make test`; same qrun rule as above.
- Do not pipe qrun or chain commands after it (cmd_guard denies it); `cd build && python3 -I .../qrun.py -- ctest` is fine.
- Do not prefix a test command with `env` or `VAR=value`; qrun sets the test env.

## Traps
- Stale build dir: after a CMake change that looks ignored, delete only your own `build/` and configure again.
- Undefined behaviour passes tests by luck: signed overflow, out-of-range index, use after move, dangling `string_view`. The sanitizer run finds it.
- Header changes rebuild everything: keep includes minimal, forward-declare where you can.
- Ownership: `std::unique_ptr` by default, no raw `new`/`delete`, no owning raw pointers; pass `const T&` or `std::span` for views.
- `auto` copies: write `const auto&` in range-for over containers of non-trivial types.
- Do not compare floats with `==`; do not use `std::endl` in loops.
- Order of static initialisation across translation units is undefined: no globals that depend on other globals.
- Tests must not depend on the working directory or on test order; use `ctest` fixtures for shared setup.
