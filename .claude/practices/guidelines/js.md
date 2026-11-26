# JavaScript guidelines

Digest of common modern-JS practice (MDN, the ECMAScript spec, ESLint recommended). Commands and traps are in `practices/js.md`. TypeScript projects also read `typescript.md`. The project's own CLAUDE.md wins.

## Language
- ES modules (`import`/`export`), `const` by default, `let` when reassigned, never `var`. `===` and `!==` only.
- Use the platform before a dependency: `fetch`, `URL`, `structuredClone`, `Intl`, `AbortController`, `Array.prototype.at/flatMap`, optional chaining `?.` and `??`. No lodash for one function.
- Async: `async`/`await` over raw promise chains; always handle rejection (`try/catch` at a boundary or `.catch`). Run independent awaits together with `Promise.all`/`allSettled`. No `async` function passed to `forEach`.
- No floating promises: every promise is awaited, returned, or deliberately detached with a comment.
- Prefer pure functions and plain objects. Do not mutate arguments. Copy with spread or `structuredClone`.
- Money and ids are not floats: use integers (cents) or strings; dates through `Intl` (and `Temporal` only if the project has a polyfill or target support), store UTC.

## Safety
- Treat all external input as untrusted: validate shape and type at the boundary (a schema library or explicit checks).
- Never build HTML from strings: use `textContent`, DOM APIs or the framework's escaping; never `innerHTML`, `eval`, `new Function`, or `document.write` with user data. Escape on output.
- No secrets in client code or in the repo. Use `crypto.getRandomValues`/`crypto.randomUUID`, not `Math.random`, for ids and tokens.
- Node: no shell strings (`child_process.exec` with interpolation); use `execFile`/`spawn` with an argument array. Resolve and check file paths against a base directory.

## Structure
- One responsibility per module; named exports over default exports; no circular imports (`madge` in the base image checks).
- No dead code or unused exports (`knip`); no copy-pasted blocks (`jscpd`); ESLint clean with no blanket disables.
- Browser code: keep event handlers small, remove listeners and timers you add, make interactive elements real `<button>`/`<a>` with labels and keyboard support.

## DRY and SOLID in JS
Base rules apply. In JS they take these forms:
- Pure functions and ES modules over classes. A class only when it holds state with a lifecycle.
- Open/closed: a table or `Map` from key to handler (`const handlers = { create, remove }`) beats a growing `if`/`switch` chain. Adding a case adds one entry.
- Dependency inversion: pass the seam in as an argument (`{ fetch, now, storage }`), defaulting to the real global. Tests pass a fake.
- Composition over inheritance: combine small functions or objects; no `extends` for reuse.
- Constants, URLs, storage keys and message names live in one module and are imported. A literal repeated across files will drift.
- Barrel files (`index.js` re-exporting a folder) invite import cycles. Import from the defining module.

## Tests (TDD)
Test behaviour through the public function, not internals (see base.md).
- Runner: `node --test` (one test: `node --test --test-name-pattern="name" file`), vitest (`vitest run path -t "name"`), jest (`jest path -t "name"`). Use what the project has.
- Time: fake timers (`vi.useFakeTimers()`, `jest.useFakeTimers()`, `mock.timers` in `node:test`); restore real timers in teardown. No real `setTimeout` waits.
- Async: `await` every call. No un-awaited promise in a test. Assert failure with `await assert.rejects(...)` or `await expect(p).rejects.toThrow(...)`, never `try/catch` that passes when nothing throws.
- Mocks (`vi.mock`, `jest.mock`, `mock.method`) only at boundaries: network, clock, storage, process. Prefer a small fake passed in as an argument.
- No real network. Stub `fetch` or inject it. A test server listens on port `0` (OS picks a free port) and is closed in teardown.
- DOM and component tests: query by role, label and visible text (`getByRole`, `getByText`), not by class, id or component internals. Fire events as a user would.
- No snapshot test of a large object or DOM tree: assert the few fields that matter. A small inline snapshot is fine.
- Reset shared state between tests: module state (`vi.resetModules()`, `jest.resetModules()`), globals (`vi.unstubAllGlobals()`, restore what you assign), mocks (`restoreAllMocks`), `document.body`.
- Browser extension code: stub `chrome.*` with a small hand-written fake (`chrome.storage.local.get/set`, `chrome.runtime.sendMessage`) installed on `globalThis` per test and removed after. Do not load the real browser.
- Each test cleans up servers, timers, listeners and temp files, even when it fails. Synthetic fixtures only.

## Check in review
Unhandled rejection, `==`, string-built HTML or shell, `Math.random` for a token, mutable shared state, unbounded retry or polling, missing `await`, a listener or timer never cleared, a new dependency for a few lines.
- A handler chain or `switch` that grows at every new case instead of a table.
- A real timer, network call or fixed port in a test.
- A barrel file that closes an import cycle.
