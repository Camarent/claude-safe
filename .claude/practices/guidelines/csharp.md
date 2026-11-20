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
- Prefer composition and small interfaces at real seams. Use constructor injection, no service locator, no static mutable state. A class has one reason to change. Seal classes not designed for inheritance.
- Throw specific exceptions (`ArgumentException` family, `InvalidOperationException`), never `Exception` itself; do not catch `Exception` except at a top-level boundary that logs and rethrows or translates. Never swallow. Use `throw;` not `throw ex;`. Exceptions are for exceptional cases; use `TryParse`-style APIs for expected failures.
- Public APIs: XML doc comments with parameters, returns and exceptions; avoid breaking signatures (add overloads); do not expose `List<T>` or mutable internals.

## Tests (TDD)
Cycle and rules are in base.md. C# forms:
- xUnit: `[Fact]` for one case, `[Theory]` with `[InlineData]` (or `MemberData`) for a table of cases. Run one: `dotnet test --filter "FullyQualifiedName~Name"`.
- Name `Method_State_Expected` (`Parse_EmptyInput_ThrowsFormatException`). One claim per test.
- Exceptions: `Assert.Throws<T>` and `await Assert.ThrowsAsync<T>`; assert on the type and a key part of the message. Never `try`/`catch` in a test.
- Cleanup: `IDisposable` or `IAsyncLifetime` (`InitializeAsync`, `DisposeAsync`) on the test class. xUnit builds a new class instance per test, so the constructor is the per-test setup. Use `IClassFixture<T>` for costly shared state.
- Time: inject `TimeProvider` (.NET 8+; `FakeTimeProvider` from `Microsoft.Extensions.TimeProvider.Testing`). On older targets use your own `IClock`. No `DateTime.Now` or `Task.Delay` in code under test.
- Fakes over mocks: a small class implementing the interface, kept in the test project. Moq or NSubstitute only at boundaries (HTTP, queue, clock, third-party SDK), never for your own domain classes.
- HTTP integration: `WebApplicationFactory<Program>`; replace services in `ConfigureTestServices`. Data tests: Testcontainers or a scratch database per run, removed at the end. EF Core `InMemory` does not behave like a real database: do not trust it for queries or constraints.

## DRY and SOLID (C# forms)
- Open/closed: strategy by DI (`IEnumerable<IHandler>` registered in the container) or a `Dictionary<Key, Handler>`; a new case is a new class or entry. A `switch` on type repeated at several sites is the missing strategy.
- Split interfaces by consumer: `IOrderReader` and `IOrderWriter`, not `IOrderRepository` with twelve members.
- Register services in the DI container; pass `TimeProvider` rather than calling `DateTime`. No `IServiceProvider.GetService` in business code.
- `record` for data and value objects: equality and `with` come free; share a rule by one method on the record, not copies.
- Share behaviour with an extension method, not a base class.
- Configuration keys, regexes and rules live in one place: a constant or an options class bound with `IOptions<T>`.

## Safety
Parameterised queries (EF Core/Dapper parameters), no string-built SQL; validate and encode untrusted input; no secrets in source or `appsettings` in the repo (user-secrets, env, or a vault); `RandomNumberGenerator`, not `Random`, for tokens; no `BinaryFormatter`.

## Tooling (must be clean)
`dotnet build -warnaserror`, `dotnet format --verify-no-changes`, analyzers (`Microsoft.CodeAnalysis.NetAnalyzers`, Roslynator or StyleCop if the project has them), `dotnet test`, `dotnet list package --vulnerable`.

## Check in review
Blocking on a task, missing cancellation token, undisposed resource, `catch (Exception)` that hides an error, public mutable collection, `DateTime.Now` where `DateTimeOffset.UtcNow` is meant, culture-dependent parsing, null-forgiving that hides a real null, a rule or constant written in two places, a `switch` on a type repeated at several call sites, a test that cannot fail (no assert, or asserts a value it just set), a mock of your own domain class, `Thread.Sleep`, `Task.Delay` or the real clock in a test, a service locator, an interface with one implementation and no seam.
