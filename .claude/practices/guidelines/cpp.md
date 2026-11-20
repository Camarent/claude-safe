# C++ guidelines

Digest of the C++ Core Guidelines (isocpp.github.io/CppCoreGuidelines). Rule ids in brackets. Gates and commands are in `practices/cpp.md`. The project's own CLAUDE.md wins.

## Ownership and lifetime
- Express ownership in the type: value first, `std::unique_ptr` for sole ownership, `std::shared_ptr` only for real shared ownership. No owning raw pointers, no `new`/`delete` in application code [R.1, R.3, R.11].
- RAII for every resource (files, locks, sockets, handles). The destructor releases it [E.6].
- Rule of zero: let members manage themselves. If you define one of copy/move/destructor, define or delete all five [C.20, C.21].
- A non-owning view (`std::string_view`, `std::span`, raw pointer, reference) must not outlive its owner. Never return a view of a local [F.43, ES.65].
- Moved-from objects are only assigned to or destroyed [ES.56, C.64].

## Interfaces
- Pass cheap types by value, others by `const&`; a sink parameter by value then `std::move` [F.16, F.18].
- Return values, not output parameters; use structs or `std::pair`/tuples for several results [F.20, F.21].
- Make interfaces precise and strongly typed (an enum class or a small struct, not `int` flags or `bool` pairs) [I.4, I.1].
- `const` by default; `constexpr` for compile-time values; `noexcept` only where it truly cannot throw [Con.1, F.6].
- Single-argument constructors are `explicit`. Virtual functions are marked `override` or `final`. A base class has a public virtual or a protected non-virtual destructor [C.46, C.128, C.35].

## Errors and safety
- Exceptions for errors that cannot be handled locally; do not use them for control flow. Never throw from a destructor [E.2, E.16].
- Prefer algorithms and range-for over index loops; no C arrays or `memcpy` on non-trivial types; `std::array`/`std::vector` and `at()` or checked access at boundaries [SL.con.1, ES.71].
- No C-style casts; `static_cast` when needed, `dynamic_cast` only with a reason. Initialise every variable at declaration [ES.48, ES.20].
- Avoid macros and non-const globals; use `enum class`, `constexpr` and `inline` variables [ES.30, I.2].
- Concurrency: no data races; guard shared state with `std::scoped_lock`, never hold a lock across a call to unknown code; prefer tasks/futures over raw threads; join or use `std::jthread` [CP.2, CP.20, CP.22, CP.25].

## Style that matters
- Headers: include guards or `#pragma once`, self-contained, no `using namespace` at file scope [SF.8, SF.7].
- Name things for what they are; keep functions short and single-purpose; no premature optimisation [NL.1, F.2, Per.1].
- Concepts or `static_assert` for template requirements instead of deep SFINAE (C++20).

## Check in review
Owning raw pointer, missing `override`, `const&` to a temporary stored, dangling view, unchecked index, missing RAII on an error path, a lock held over a callback, a destructor that can throw. Run the sanitizer build from `practices/cpp.md`: UB passes tests by luck.
