# Custom Serializers

This guide shows how to send generated request bodies in a format Schemathesis does not serialize by default, such as CSV or MessagePack.

!!! note "Custom Serializers vs Media Types"
    Use custom **serializers** when you have a JSON Schema describing your data structure but need a different output format. Use custom [media types](custom-media-types.md) when you need to generate raw data without a schema structure (like PDFs or images).

## Prerequisites

- Schemathesis installed or available via `uvx`
- An API operation whose request body schema describes the data, declared under a media type such as `text/csv`

## Serialize a JSON Schema body as CSV

### 1. Describe the rows in the schema

```yaml
paths:
  /upload-users:
    post:
      requestBody:
        required: true
        content:
          text/csv:
            schema:
              type: array
              items:
                type: object
                required: [first_name, last_name]
                properties:
                  first_name:
                    type: string
                    pattern: "^[A-Za-z]+$"
                  last_name:
                    type: string
                    pattern: "^[A-Za-z]+$"
      responses:
        "201":
          description: Users created
        "400":
          description: Invalid CSV
```

Schemathesis generates lists of dictionaries for this schema:

```python
[{"first_name": "John", "last_name": "Doe"}, {"first_name": "Jane", "last_name": "Smith"}]
```

The schema does not set `additionalProperties: false`, so generated rows can also contain extra keys.

### 2. Register a serializer

A serializer receives a context and the generated value, and returns the request body as `bytes`:

```python
# csv_serializer.py
import csv
from io import StringIO

import schemathesis

FIELDS = ["first_name", "last_name"]


def to_csv(rows, delimiter=","):
    output = StringIO()
    # Skip keys the schema does not declare; fill missing ones with ""
    writer = csv.DictWriter(output, FIELDS, delimiter=delimiter, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


@schemathesis.serializer("text/csv")
def csv_serializer(ctx, value):
    # Examples from the schema may already be raw bytes
    if isinstance(value, bytes):
        return value
    # Negative testing can generate values that are not lists of objects
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return str(value).encode("utf-8")
    return to_csv(value)
```

If the serializer returns `None`, the request is sent without a body.

### 3. Load the serializer and run

```bash
export SCHEMATHESIS_HOOKS=csv_serializer
uvx schemathesis run http://localhost:8000/openapi.json
```

The `/upload-users` operation receives CSV bodies such as:

```
first_name,last_name
KvPdoRUIixtsQr,TCTwMvpQMWODkTSN
JvbanB,F
```

Without a registered `text/csv` serializer, Schemathesis sends the generated value as plain text: the string form of the Python list, not CSV.

## Reuse a built-in serializer

To send a custom media type with an existing serializer (YAML, JSON, XML), register an alias:

```python
import schemathesis

# Reuse the built-in YAML serializer for a non-standard YAML media type
schemathesis.serializer.alias("application/x-yaml-custom", "application/yaml")

# Reuse JSON for an internal media type
schemathesis.serializer.alias("application/vnd.company.internal", "application/json")

# Register multiple aliases at once
schemathesis.serializer.alias(["text/x-json", "application/jsonrequest"], "application/json")
```

Media types with `+json` or `+xml` suffixes (like `application/vnd.api+json`) are handled without aliases, as are `text/json`, `application/x-json`, `application/jwt` and `application/jose+jwe`.

## Register one serializer for several media types

Pass every media type to the decorator. This reuses `to_csv` from step 2:

```python
@schemathesis.serializer("text/csv", "text/comma-separated-values", "application/csv")
def csv_serializer(ctx, value):
    if isinstance(value, bytes):
        return value
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return str(value).encode("utf-8")
    return to_csv(value)
```

## Change serialization per operation

`ctx.case` is the test case being serialized. This version sends tab-separated rows to `/bulk-import` and comma-separated rows elsewhere:

```python
@schemathesis.serializer("text/csv")
def csv_serializer(ctx, value):
    if isinstance(value, bytes):
        return value
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return str(value).encode("utf-8")
    delimiter = "\t" if ctx.case.path == "/bulk-import" else ","
    return to_csv(value, delimiter=delimiter)
```

Serializers apply to every transport: network requests, ASGI and WSGI apps.

## Troubleshooting

**`ValueError: dict contains fields not in fieldnames`.** Generated rows contain keys that are not in the header. Pass `extrasaction="ignore"` to `csv.DictWriter`, or add `additionalProperties: false` to the row schema.

**"API accepted schema-violating request" with a value like `False` in a text column.** CSV carries no types, so a negative case that replaces a string with the boolean `false` reaches the API as the valid string `False`. Run CSV endpoints with `--mode positive`, or check which value the API received in the reproduction command before treating it as a bug.

**The request body is not CSV.** Check that `SCHEMATHESIS_HOOKS` names the module (without `.py`) and that the media type in the decorator matches the one in the schema.

## What's Next

- **[Custom Media Types](custom-media-types.md)** - Generate raw data when there's no JSON Schema
- **[Extending Schemathesis](extending.md)** - Other customization options
- **[Serialization API Reference](../reference/python.md#serialization)**
