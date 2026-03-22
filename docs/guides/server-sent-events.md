# Testing Server-Sent Events

Schemathesis validates `text/event-stream` responses by parsing the stream into individual events and checking each one against the schema you define. No special configuration is required.

## Defining an SSE endpoint

Use `itemSchema` (OpenAPI 3.2) to describe a single event's shape:

```yaml
paths:
  /events:
    get:
      responses:
        "200":
          description: Live event stream
          content:
            text/event-stream:
              itemSchema:
                type: object
                required: [type, payload]
                properties:
                  type:
                    type: string
                  payload:
                    type: object
```

On OpenAPI 3.1 and earlier, use `schema` in place of `itemSchema` — Schemathesis treats it as the per-event schema.

## Parsed fields

| Field | Notes |
|---|---|
| `data` | Multiple `data:` lines are joined with newlines |
| `event` | Defaults to `"message"` if omitted |
| `id` | Persists across events until reset; null characters are ignored |
| `retry` | Reconnection delay in milliseconds |

Events with no `data` lines are skipped — they produce no validation result. Comment lines (starting with `:`) are ignored.

Schemathesis validates each parsed event against `itemSchema`. When validation fails, the error message identifies which event failed:

```
- SSE event violates schema

  Event #1: 'type' is a required property
```

Failures are deduplicated — if the same schema path fails across multiple events, it appears once.

## Polymorphic events

Use `oneOf` or `anyOf` when different event types have different shapes:

```yaml
itemSchema:
  oneOf:
    - type: object
      required: [type, message]
      properties:
        type:
          const: chat
        message:
          type: string
    - type: object
      required: [type, userId]
      properties:
        type:
          const: presence
        userId:
          type: string
```

With `oneOf`, each event must match exactly one branch. Using a discriminator field with `const` — as above — keeps branches mutually exclusive and produces precise failure messages when an event matches none. Use `anyOf` if events are allowed to satisfy multiple branches simultaneously.

## Embedded payloads

When a `data` field carries a serialized payload, describe it with `contentMediaType` and `contentSchema`:

```yaml
itemSchema:
  type: object
  properties:
    data:
      type: string
      contentMediaType: application/json
      contentSchema:
        type: object
        required: [id, status]
        properties:
          id:
            type: integer
          status:
            type: string
```

Schemathesis deserializes the `data` string using the registered deserializer for `application/json` and validates the result against `contentSchema`. A failure is reported as:

```
- SSE event payload violates content schema

  Event #2: 'id' is a required property
```

For media types beyond `application/json`, register a custom deserializer — see [Custom Response Deserializers](custom-response-deserializers.md).

## Current limitations

Schemathesis reads the full response body before parsing. An infinite or long-lived SSE stream will block until the connection times out or the server closes it. Use endpoints that close the stream after emitting their events.
