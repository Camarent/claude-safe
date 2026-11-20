# JavaScript guidelines

Digest of common modern-JS practice (MDN, the ECMAScript spec, ESLint recommended). Commands and traps are in `practices/js.md`. TypeScript projects also read `typescript.md`. The project's own CLAUDE.md wins.

## Language
- ES modules (`import`/`export`), `const` by default, `let` when reassigned, never `var`. `===` and `!==` only.
- Use the platform before a dependency: `fetch`, `URL`, `structuredClone`, `Intl`, `AbortController`, `Array.prototype.at/flatMap`, optional chaining `?.` and `??`. No lodash for one function.
- Async: `async`/`await` over raw promise chains; always handle rejection (`try/catch` at a boundary or `.catch`). Run independent awaits together with `Promise.all`/`allSettled`. No `async` function passed to `forEach`.
- No floating promises: every promise is awaited, returned, or deliberately detached with a comment.
- Prefer pure functions and plain objects. Do not mutate arguments. Copy with spread or `structuredClone`.
- Money and ids are not floats: use integers (cents) or strings; dates through `Intl`/`Temporal`, store UTC.

## Safety
- Treat all external input as untrusted: validate shape and type at the boundary (a schema library or explicit checks).
- Never build HTML from strings: use `textContent`, DOM APIs or the framework's escaping; never `innerHTML`, `eval`, `new Function`, or `document.write` with user data. Escape on output.
- No secrets in client code or in the repo. Use `crypto.getRandomValues`/`crypto.randomUUID`, not `Math.random`, for ids and tokens.
- Node: no shell strings (`child_process.exec` with interpolation); use `execFile`/`spawn` with an argument array. Resolve and check file paths against a base directory.

## Structure
- One responsibility per module; named exports over default exports; no circular imports (`madge` in the base image checks).
- No dead code or unused exports (`knip`); no copy-pasted blocks (`jscpd`); ESLint clean with no blanket disables.
- Browser code: keep event handlers small, remove listeners and timers you add, make interactive elements real `<button>`/`<a>` with labels and keyboard support.

## Tests
`node --test` or the project's runner; synthetic fixtures; fake timers instead of real waits; each test cleans up servers, timers and temp files. Test behaviour through the public function, not internals.

## Check in review
Unhandled rejection, `==`, string-built HTML or shell, `Math.random` for a token, mutable shared state, unbounded retry or polling, missing `await`, a listener or timer never cleared, a new dependency for a few lines.
