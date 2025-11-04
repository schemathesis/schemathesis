# Custom Response Deserializers

Schemathesis validates API responses against JSON Schema definitions. JSON and YAML responses work automatically. For other formats like MessagePack or domain-specific encodings, register a custom deserializer.

## Quick Start

```python
import schemathesis

@schemathesis.deserializer("application/vnd.custom")
def decode_custom(ctx: schemathesis.DeserializationContext, response):
    try:
        text = response.content.decode(response.encoding or "utf-8")
        key, value = text.split("=", 1)
        return {key: value}
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid custom format: {exc}")
```

- Register one or more media types in the decorator
- Return the deserialized Python object for schema validation
- Raise appropriate exceptions if decoding fails; Schemathesis surfaces them in reports

## Context & Introspection

The `ctx` parameter provides access to test metadata:

- `ctx.operation`: The `APIOperation` being tested
- `ctx.case`: The generated `Case` when testing with data generation (may be `None` when validating responses directly via `APIOperation.validate_response()`)

Example using context:

```python
import msgpack

@schemathesis.deserializer("application/vnd.api+msgpack")
def deserialize_msgpack(ctx: schemathesis.DeserializationContext, response):
    try:
        data = msgpack.unpackb(response.content, raw=False)

        # Access operation metadata if needed
        if ctx.operation.method == "GET":
            # Handle GET responses differently
            pass

        return data
    except Exception as exc:
        raise ValueError(f"Failed to decode MessagePack: {exc}")
```

## Best Practices

- **Handle inputs defensively**: Generated test cases and responses can contain unexpected or malformed data, especially during negative testing
- **Raise descriptive exceptions**: Include context about what went wrong to aid debugging
