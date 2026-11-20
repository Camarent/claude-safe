# C++ guidelines

Digest of the C++ Core Guidelines (isocpp.github.io/CppCoreGuidelines). Rule ids in brackets. Gates and commands are in `practices/cpp.md`. The project's own CLAUDE.md wins.

## Ownership and lifetime
- Express ownership in the type: value first, `std::unique_ptr` for sole ownership, `std::shared_ptr` only for real shared ownership. No owning raw pointers, no `new`/`delete` in application code [R.1, R.3, R.11].
- RAII for every resource (files, locks, sockets, handles). The destructor releases it [E.6].
- Rule of zero: let members manage themselves. If you define one of copy/move/destructor, define or delete all five [C.20, C.21].
- A non-owning view (`std::string_view`, `std::span`, raw pointer, reference) must not outlive its owner. Never return a view of a local [F.43, ES.65].
- Moved-from objects are only assigned to or destroyed [ES.56, C.64].

## Interfaces
- Pass cheap types by value, others by `const&`; a sink parameter: take it by value then `std::move` [F.16], or `X&&` and `std::move` it [F.18].
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

## Tests (TDD)
The Core Guidelines have no TDD rules; this follows `base.md` with C++ tools. Use the framework the project already has (GoogleTest or Catch2).
- Red: write one test, build it, run only it, see it fail for the right reason. A compile error is not red. Green: least code. Refactor: suite green first.
- Run one test fast: `ctest -R name --output-on-failure`, or the binary with `--gtest_filter=Suite.Name` (Catch2: `"test name"` or `[tag]`).
- Shared setup goes in a fixture (`TEST_F`, Catch2 `TEST_CASE_METHOD`). Several inputs with one claim go in a parameterised test (`TEST_P`, `GENERATE`), not a loop with one assert.
- Death tests (`EXPECT_DEATH`) only for contract violations (`Expects`, `assert`), never for ordinary errors. Test ordinary errors with `EXPECT_THROW` or the returned error value.
- Seams: depend on an abstract interface or a template parameter at I/O, clock and network boundaries. Pure code needs no seam.
- Fake over mock: a small in-memory fake of the interface first. gMock (`MOCK_METHOD`) only at a real boundary, never for your own classes, and do not pin call order unless order is the contract.
- No `sleep_for`, no `system_clock::now()` in the unit under test. Inject a clock (an interface or a callable) and advance it in the test.
- Temp files and dirs are RAII objects that delete on scope exit, even when an assertion fails. No fixed paths in `/tmp`.
- Run the suite under ASan and UBSan (`-fsanitize=address,undefined`), and under TSan for threaded code. A sanitizer report is a failing test. Never suppress one without a `shortcut:` note. Run the build from `practices/cpp.md`.

## DRY and SOLID
Base rules are in `base.md`; these are the C++ forms.
- Generic code: write one template with a concept instead of copy-pasted overloads per type [T.2, T.10]. Pass operations as callables to algorithms [T.40].
- Mix generic and OO deliberately: a template for compile-time variation, a virtual interface for run-time variation [T.5]. Do not use both for one seam.
- Composition over inheritance: hold a member and forward. Inheritance only for a real is-a with inherent hierarchy, never to reuse code [C.120].
- An interface is a pure abstract base: no data, no non-trivial logic [C.121, C.122, I.25]. Keep interface inheritance apart from implementation inheritance [C.129].
- A polymorphic class suppresses copy and move [C.67].
- Avoid protected data; keep members private and expose behaviour, not state [C.133, C.9].
- A class receives its collaborators through the constructor, as `unique_ptr`, reference or template parameter. No singletons, no hidden globals [I.3, I.2].
- One reason to change per class.
- A class owns data and its invariant; plain data that varies freely is a struct [C.2].
- A function does one logical operation [F.2].
- Keep the argument list short; group related arguments in a struct [I.23, C.1].
- Interfaces: see `base.md` on segregation and single-implementation layers.

## Check in review
Owning raw pointer, missing `override`, `const&` to a temporary stored, dangling view, unchecked index, missing RAII on an error path, a lock held over a callback, a destructor that can throw. UB passes tests by luck: see the sanitizer rule under Tests.
- Duplicated logic across overloads that one template or helper would remove.
- Inheritance used for reuse, a public data member in a base, an interface with one implementation and no seam.
- A test that cannot fail (no assertion on the outcome, or the assertion repeats the code), a mock of own code, a `sleep_for` or real clock in a test.
