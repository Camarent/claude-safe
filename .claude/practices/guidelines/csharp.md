# C# guidelines

Digest of the .NET Framework Design Guidelines and Microsoft's C# coding conventions (learn.microsoft.com/dotnet/csharp/fundamentals/coding-style/coding-conventions, learn.microsoft.com/dotnet/standard/design-guidelines). The project's own CLAUDE.md and `.editorconfig` win.

## Language
- Enable nullable reference types and treat nullable warnings as errors (`<Nullable>enable</Nullable>`, `<TreatWarningsAsErrors>`). No `!` null-forgiving operator without a one-line reason. Check arguments at public boundaries (`ArgumentNullException.ThrowIfNull`).
- Prefer `record` / `record struct` for immutable data, `required` and `init` members, pattern matching and switch expressions for branching on shape. `readonly` and immutability by default.
- `var` when the type is obvious from the right side; explicit type otherwise. File-scoped namespaces. Expression-bodied members only when they stay one line.
- Collections: expose `IReadOnlyList<T>`/`IReadOnlyCollection<T>` or `IEnumerable<T>`, accept the most general type you need. Use LINQ for clarity, but not in hot paths or with side effects; materialise once.
- Strings: `StringBuilder` for loops, interpolation otherwise; pass `StringComparison` explicitly; `CultureInfo.InvariantCulture` for parsing and storage.

## Async and resources
- `async`/`await` all the way: no `.Result`, `.Wait()` or `GetAwaiter().GetResult()` on async code. Name async methods `...Async`, take a `CancellationToken` as the last parameter and pass it down. Never `async void` except event handlers. Use `Task.WhenAll` for independent work.
- Dispose what you own: `using` declarations for `IDisposable`/`IAsyncDisposable`. Do not hold a `HttpClient` per request; use `IHttpClientFactory` or one shared instance.
- `lock` on a private object only, never on `this` or a type; keep critical sections short; prefer `Interlocked`, concurrent collections, or channels over hand-rolled locking.

## Design (Framework Design Guidelines)
- Names: `PascalCase` for types, members and public fields; `camelCase` for locals and parameters; `_camelCase` for private fields; `I` prefix for interfaces; no Hungarian notation, no abbreviations.
- Prefer composition and small interfaces at real seams; constructor injection for dependencies, no service locator, no static mutable state. A class has one reason to change. Seal classes not designed for inheritance.
- Throw specific exceptions (`ArgumentException` family, `InvalidOperationException`), never `Exception` itself; do not catch `Exception` except at a top-level boundary that logs and rethrows or translates. Never swallow. Use `throw;` not `throw ex;`. Exceptions are for exceptional cases; use `TryParse`-style APIs for expected failures.
- Public APIs: XML doc comments with parameters, returns and exceptions; avoid breaking signatures (add overloads); do not expose `List<T>` or mutable internals.

## Safety
Parameterised queries (EF Core/Dapper parameters), no string-built SQL; validate and encode untrusted input; no secrets in source or `appsettings` in the repo (user-secrets, env, or a vault); `RandomNumberGenerator`, not `Random`, for tokens; no `BinaryFormatter`.

## Tooling (must be clean)
`dotnet build -warnaserror`, `dotnet format --verify-no-changes`, analyzers (`Microsoft.CodeAnalysis.NetAnalyzers`, Roslynator or StyleCop if the project has them), `dotnet test`, `dotnet list package --vulnerable`.

## Check in review
Blocking on a task, missing cancellation token, undisposed resource, `catch (Exception)` that hides an error, public mutable collection, `DateTime.Now` where `DateTimeOffset.UtcNow` is meant, culture-dependent parsing, null-forgiving that hides a real null.
