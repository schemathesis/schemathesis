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

On OpenAPI 3.0 and 3.1, use `schema` in place of `itemSchema` - Schemathesis treats it as the per-event schema.

## Constraining the whole stream

In OpenAPI 3.2, `schema` describes the whole stream, with its events read as an array in arrival order. Use it for constraints that span events, alongside `itemSchema` for the shape of each one:

```yaml
text/event-stream:
  itemSchema:
    $ref: "#/components/schemas/Event"
  schema:
    type: array
    maxItems: 100
```

A violation is reported once for the response:

```
- SSE stream violates schema

  [...] has more than 100 items
```

The same applies on OpenAPI 3.0 and 3.1 when `itemSchema` is present next to `schema`.

A stream Schemathesis stops reading early is checked event by event only - see [Endless streams](#endless-streams).

## Parsed fields

| Field | Notes |
|---|---|
| `data` | Multiple `data:` lines are joined with newlines |
| `event` | Defaults to `"message"` if omitted |
| `id` | Persists across events until reset; null characters are ignored |
| `retry` | Omitted from the parsed event if the value is not a valid integer |

Events with no `data` lines are skipped - they produce no validation result. Comment lines (starting with `:`) are ignored.

Schemathesis validates each parsed event against `itemSchema`. When validation fails, the error message identifies which event failed:

```
- SSE event violates schema

  Event #1: 'type' is a required property
```

Failures are deduplicated - if the same schema path fails across multiple events, it appears once.

## Polymorphic events

Use `oneOf` when different event types have different shapes:

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

Each event must match exactly one branch. Using a discriminator field with `const` keeps branches mutually exclusive and produces precise failure messages when an event matches none.

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

For media types beyond `application/json`, register a custom deserializer - see [Custom Response Deserializers](custom-response-deserializers.md).

## Endless streams

Schemathesis reads a stream until one of these happens, then validates the events received so far:

- the server closes it
- 20 events have arrived
- the request timeout passes, counted from when the response starts

When it stops early, an event that was still arriving is dropped, and the whole-stream `schema` is not checked: the events received are only the start of the stream. Change the event limit with [`max-stream-events`](../reference/configuration.md#max-stream-events):

```toml
max-stream-events = 100
```

A stream that sends events slower than that waits out the timeout, so lower it for such APIs:

```toml
request-timeout = 5
```
