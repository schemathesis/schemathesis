# How Schemathesis Adapts During a Run

Once a test starts, Schemathesis watches what the API returns and updates its plan within the same run — this is what distinguishes it from a one-shot input generator.

!!! note
    The behaviours below apply to OpenAPI APIs. GraphQL pipelines support stateful chaining but not the constraint-learning loop today.

## What gets learned, from where

Four kinds of runtime signal feed adaptation. Exact thresholds shift across releases; the categories don't.

- **Schema constraints from error responses** — rejected positive-mode 4xx responses with a recognised framework envelope yield validation rules (required fields, formats, bounds, enums, patterns, type mismatches, unknown-property rejections) that get applied to the operation's schema. Single hits are ignored as noise.
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

A schema declares `POST /events` with a `scheduled_at` field typed as a plain `string` — no `format`. The first case sends a random string; the server returns a Spring 400 envelope:

```json
{
  "timestamp": "2026-03-14T10:00:00.000+0000",
  "status": 400,
  "error": "Bad Request",
  "message": "JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String \"qK7\""
}
```

The parser extracts a type-mismatch observation pointing at `scheduled_at`. Once it fires a second time, the field's schema gains `format: date`, and subsequent cases generate valid dates like `"2026-03-14"` — no schema or test edits required.

## Related

- **[Stateful Testing](stateful.md)** — how operations are chained and the resource pool is populated.
- **[Data Generation](data-generation.md)** — the four phases at a higher level.
