# How Schemathesis Adapts During a Run

Once a test starts, Schemathesis watches what the API returns and updates its plan within the same run — this is what distinguishes it from a one-shot input generator.

!!! note
    The behaviors below apply to OpenAPI APIs. GraphQL pipelines support stateful chaining but not the constraint-learning loop today.

## What gets learned, from where

Four kinds of runtime signal feed adaptation.

- **Schema constraints from error responses** — rejected positive-mode 4xx responses (other than 401 and 403) with a recognized framework envelope yield validation rules (required fields, formats, bounds, enums, patterns, type mismatches, unknown-property rejections) that get applied to the operation's schema. A single rejection is enough; the rule applies from the next checkpoint, which is when Schemathesis next starts testing that operation in a phase, or starts the next stateful suite.
- **Resource lifecycle** — successful 2xx responses populate a per-resource pool that later operations draw on; successful and "not-found" deletes mark ids as gone so they're less likely to be redrawn. A separate use-after-free check looks at the recorded scenario, independent of pool state.
- **Authentication** — a 401 or 403 on a public operation produces an inferred auth requirement; if credentials are configured for a declared scheme, subsequent calls retry with them automatically.
- **Operation health and budget allocation** — operations that don't produce useful signal (undocumented method-not-allowed responses, repeated transport timeouts in the stateful phase) get de-prioritized or skipped so budget shifts elsewhere. Connection-level failures (resets, premature disconnects) surface immediately as scenario errors.

## Where the learning lives in the phase pipeline

- **Examples** — exercises spec-declared examples; learned constraints evict examples that no longer match.
- **Coverage** — generates deterministic boundary cases, with learned constraints folded in as cases are produced.
- **Fuzzing** — picks up everything learned earlier at each new scenario.
- **Stateful** — chains operations using the live resource pool and inferred auth.

Most learning happens in Examples and Coverage; Fuzzing and Stateful pick it up at scenario boundaries, not per case.

## Worked example

A schema declares `POST /events` with a `scheduled_at` field typed as a plain `string` — no `format`. The first case sends a random string; the server returns Spring's default 400 envelope carrying a Jackson error:

```json
{
  "timestamp": "2026-03-14T10:00:00.000+0000",
  "status": 400,
  "error": "Bad Request",
  "message": "JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String \"xQ7kPz\""
}
```

The message names the rejected value but not the field, so Schemathesis looks for `"xQ7kPz"` in the request it sent and finds it in `scheduled_at`. From the next checkpoint on, the field's schema has `format: date`, and subsequent cases generate valid dates like `"2026-03-14"` — no schema or test edits required.

Attribution by value needs a value that identifies one field: values shorter than four characters, `true`, `false`, and `null` are not attributed, and neither is a value that appears in more than one place in the request. When the message includes Jackson's `through reference chain: ...`, the field comes from the chain instead.

## Reusing response data across operations

When fuzzing `GET /users/{id}`, a random ID only reaches an existing user by chance. Small sequential integers match now and then; UUIDs and other opaque identifiers practically never do, so nearly every request returns 404. Error handling gets thoroughly tested, but success logic — response schema validation, data serialization, permission checks — remains largely untouched.

Schemathesis captures useful values from successful responses into the resource pool and reuses them when generating test cases. Dependency analysis identifies which operations produce resources and which consume them. For example, it recognizes that `POST /users` creates users with IDs, and `GET /users/{id}` needs those IDs.

Captured values augment random generation. `GET /users/{id}` is tested with both random IDs (finding 404 handling bugs) and real IDs from earlier `POST /users` calls (finding bugs in success paths).

The examples, coverage, and fuzzing phases all contribute to and draw from the pool: a `POST /users` example populates it, and `GET /users/{id}` examples use the captured ID to reach code paths that random IDs rarely hit. The pool is updated after each operation's test run finishes, so values captured from one operation are available to operations tested after it. Operations are ordered so producers run before their consumers. The [stateful phase](stateful.md) draws from the same pool for parameters its links do not fill, and adds the responses it receives.

Per-phase control is under [`phases.<phase>.extra-data-sources`](../reference/configuration.md#phasesphaseextra-data-sources).

## Trade-off: learned constraints narrow generation

A learned constraint makes positive-mode generation match what the server accepts, so more requests reach business logic. It also means Schemathesis sends fewer positive-mode values that the server's validation rejects, so a bug hidden behind that validation (a crash on a malformed date, for example) is less likely to be found through positive-mode cases. Negative-mode cases are not recorded and keep probing invalid inputs.

To compare, run with error feedback disabled:

```toml
[phases.fuzzing.error-feedback]
enabled = false
```

This switches off constraint learning for the whole run, not only for the fuzzing phase.

## Carrying observations between runs

Schemathesis keeps a per-project [cache](../reference/configuration.md#cache) of discoveries: rejected requests that produced error-feedback observations, operations that need authentication beyond what the spec declares, and operations that return `405 Method Not Allowed`. On the next run it replays the cached requests during probing and keeps the observations the server still confirms, so learned constraints apply from the start of the run. The CLI shows how many requests were replayed in the `Cache:` row. Disable the cache with `cache.enabled = false`, or delete its directory to start from scratch.

## Related

- **[Stateful Testing](stateful.md)** — how operations are chained with links.
- **[Data Generation](data-generation.md)** — the four phases at a higher level.
