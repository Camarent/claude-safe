# Base guidelines (all languages)

Read on demand. A project's own CLAUDE.md and AUDIT.md win over this file. The "smallest complete change" ladder and the `shortcut:` convention are adapted from Ponytail (MIT, github.com/DietrichGebert/ponytail).

## Smallest complete change
Take the first option that fully works:
1. Does it need to exist? Skip features, options and flexibility nobody asked for; name them in one line.
2. Already in this codebase (helper, component, pattern)? Use it the way the surrounding code does.
3. Standard library or platform feature? Use it.
4. An installed dependency? Use it. Never add a dependency for a few lines.
5. Otherwise write the minimum code that works.

Be lazy about the solution, never about the change: finish every part the task needs, including callers, tests, fixtures and docs the change breaks. Deleting beats adding. Code you move or merge keeps its error handling and validation.

## Principles, with limits
- **YAGNI:** no abstraction, option, config or "for later" code nobody asked for.
- **DRY, rule of three:** copy once, extract on the third repetition. Two near-copies that must always change together are one thing: merge them. A wrong abstraction costs more than a copy. DRY covers knowledge, not text: two blocks that look alike but change for different reasons stay separate. Constants, formats and rules live in one place; the same rule written in two places will drift.
- **SOLID is a review lens, not a build target.** Use it for object-oriented code (C#, C++, TypeScript classes); other languages get the same ideas as modules and functions.
  - Single responsibility: one reason to change per class, module or function. A name that needs "and" is two things.
  - Open/closed: add behaviour by adding a case (a new strategy, handler or table entry), not by editing a growing `if`/`switch` chain at every call site.
  - Liskov: a subtype works wherever its base is used. No override that throws "not supported" or narrows what the base accepts.
  - Interface segregation: a caller depends only on what it uses. Split a wide interface by client, not by the implementer.
  - Dependency inversion: logic depends on an abstraction at a real seam (I/O, clock, database, network), and the caller passes the implementation in. Pure code needs no interface.
  - Never add an interface, layer or factory with one implementation just to satisfy these. No inheritance for reuse: use composition.
- **Small units:** split a function by job, never by line count, and never into helpers that exist only to make it shorter. Keep cyclomatic complexity under the project's gate (AUDIT.md, default 15). A deep nest is a missing early return or function.
- **Names:** say what a thing is or does in the domain's words. One word per concept across the codebase. A boolean reads as a question (`is_expired`). No abbreviations that save only a few characters, no `data`, `info`, `manager`, `util`.
- **Errors and logs:** an error message says what failed, with which value, and what to do. Log once, at the place that handles the error, not at every level it passes. Never log secrets or personal data.
- **Dependencies and state:** pass dependencies in, avoid global mutable state, keep side effects at the edges so the core stays pure and easy to test.

## Tests (TDD where it pays)
Cycle for new logic and every bug fix:
1. **Red:** write one test that states the wanted behaviour, run it, and see it fail for the right reason (a missing behaviour, not a typo or import error). Bug fix: the test reproduces the reported case first.
2. **Green:** write the least code that passes. Fix the root cause once in the shared code; grep every caller before editing. No second feature while red.
3. **Refactor:** with the suite green, remove duplication and improve names. Run the suite again. Never refactor while red.
Small steps: one behaviour per cycle, the suite runs in seconds.

When it applies: bug fix always. New non-trivial logic (branch, loop, parser, money, security, data writes): test first, or at least one test that fails when the logic breaks. Skip for trivial changes (rename, constant, glue with no branch), spikes you will throw away, and pure layout.

What a good test looks like:
- **Name states the claim:** `rejects_expired_token`, not `test_token_2`. One claim per test, so a failure names the cause.
- **Arrange, act, assert**, visibly separated. Assert the outcome (return value, stored state, emitted event), not the calls made to get there.
- **Behaviour through the public interface.** Do not test private helpers or pin the order of internal calls: a refactor must not break a test.
- **Cases that find bugs:** empty, one, many; boundary values (0, -1, max, off-by-one); invalid and hostile input; the error path; the second call (idempotence); concurrent use where the code claims safety.
- **Mocks only at real boundaries** (network, clock, filesystem, process, third-party service). Never mock the unit under test or your own pure code. Prefer a small fake over a mock chain.
- **Deterministic:** no real clock, no sleeps, no random without a seed, no network, no dependence on test order, locale or time zone. A flaky test is a bug: fix or delete it, never retry it.
- **Synthetic data**, built in the test or a fixture; never the content of a real repo, home directory or live service.
- **Cleans up** every file, process, socket, database and env change it creates, even when it fails.
- **Layers:** many fast unit tests, fewer integration tests at real seams (database, HTTP), few end-to-end tests for the main flows. A bug found at a higher layer gets a test at the lowest layer that can reproduce it.
- **Can fail:** a passing test that cannot fail is not a test. Break the code (flip a condition, drop a line) and see the test go red. Coverage shows what ran, not what is checked.

## Safety (never cut)
- Validate input at trust boundaries (user input, network, files, environment). Inside the boundary, trust the types.
- Errors that can lose data are handled or propagated, never swallowed. Write in an order that survives a crash.
- No secrets in code, logs or commits. Parameterised queries, no string-built shell or SQL. Least privilege for tokens and processes.
- Review checklist for web-facing code: OWASP Top 10 (owasp.org/Top10). For web UI: WCAG 2.2 basics (labels, contrast, keyboard use, focus).

## Shortcuts and debt
A deliberate shortcut gets a comment: `shortcut: <the limit>, <when to upgrade>`. A shortcut without an upgrade trigger rots silently. Find them with `git grep -nE '(#|//) ?shortcut:'`.

## Review rule
Every finding needs a concrete case: this input or situation leads to this wrong result. No case, no finding. No style taste, no "consider".

## Comments
Comments are API documentation: contract, units, fallback and ordering. Not the story of how the code was written. A well-named test beats a comment that defends a decision.
