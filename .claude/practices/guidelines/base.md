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
- **DRY, rule of three:** copy once, extract on the third repetition. Two near-copies that must always change together are one thing: merge them. A wrong abstraction costs more than a copy.
- **SOLID is a review lens, not a build target.** Use it for object-oriented code (C#, C++, TypeScript classes): one reason to change per class, depend on interfaces at real seams, no inheritance for reuse. Never add an interface or layer with one implementation just to satisfy it.
- **Small units:** split a function by job, never by line count, and never into helpers that exist only to make it shorter.

## Tests (TDD where it pays)
- Bug fix: write the failing test first, then fix the root cause once in the shared code. Grep every caller of the function before editing.
- New non-trivial logic (branch, loop, parser, money, security, data writes): a failing test first, or at least one test that fails when the logic breaks. Trivial changes need none.
- A test name states the claim. Tests use synthetic data, clean up what they create, and never depend on the order or content of a real repo.
- A passing test that cannot fail is not a test: check it fails against a broken version.

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
