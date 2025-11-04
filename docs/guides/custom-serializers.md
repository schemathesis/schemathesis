# Custom Serializers

Transform generated data into specialized formats like CSV, MessagePack, or other non-JSON request bodies.

## When to use custom serializers

Use custom serializers when your API accepts structured data in formats that Schemathesis doesn't support by default:

- **Binary serialization** - MessagePack, Protocol Buffers, or other compact formats
- **Data processing** - CSV, TSV, or other delimited formats for bulk operations
- **Custom text formats** - Specialized configuration files or domain-specific structures

Schemathesis generates data based on your JSON Schema but only supports common serialization formats out of the box.

!!! note "Custom Serializers vs Media Types"
    Use custom **serializers** when you have a JSON Schema describing your data structure but need a different output format. Use custom [media types](custom-media-types.md) when you need to generate raw data without a schema structure (like PDFs or images).

## Quick Start: CSV Data Processing

**OpenAPI Schema:**
```yaml
paths:
  /upload-users:
    post:
      requestBody:
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
```

This schema tells Schemathesis to generate lists of dictionaries like:
```python
[
    {"first_name": "John", "last_name": "Doe"},
    {"first_name": "Jane", "last_name": "Smith"}
]
```

Register a serializer that converts these dictionaries to CSV bytes:

```python
# csv_serializer.py
import csv
import schemathesis
from io import StringIO

@schemathesis.serializer("text/csv")
def csv_serializer(ctx, value):
    """Convert list of dictionaries to CSV bytes"""
    # Handle binary data from external examples
    if isinstance(value, bytes):
        return value

    # Handle unexpected types in negative testing  
    if not isinstance(value, list) or \
      not all(isinstance(item, dict) for item in value):
        return str(value).encode('utf-8')

    if not value:
        return b""  # Empty CSV

    # Convert dictionaries to CSV
    output = StringIO()
    field_names = sorted(value[0].keys()) if value else []
    writer = csv.DictWriter(output, field_names)
    writer.writeheader()
    writer.writerows(value)

    return output.getvalue().encode('utf-8')  # Must return bytes or None
```

```bash
export SCHEMATHESIS_HOOKS=csv_serializer
schemathesis run http://localhost:8000/openapi.json
```

**Result:** Your `/upload-users` endpoint receives properly formatted CSV data instead of JSON.

!!! note "Skipping Serialization"
    If your serializer returns `None`, the resulting request will have no body.

## Essential Patterns

### Reusing Built-in Serializers

Reuse existing serializers (YAML, JSON, XML) for custom media types:

```python
import schemathesis

# Reuse built-in YAML serializer for non-standard YAML variants
schemathesis.serializer.alias("application/x-yaml-custom", "application/yaml")

# Reuse JSON for internal company formats
schemathesis.serializer.alias("application/vnd.company.internal", "application/json")

# Register multiple aliases at once
schemathesis.serializer.alias(
    ["text/x-json", "application/jsonrequest"],
    "application/json"
)
```

**Note:** Media types with `+json` or `+xml` suffixes (like `application/vnd.api+json`) are automatically handled and don't need aliases.

### Multiple aliases for the same format

```python
@schemathesis.serializer(
    "text/csv", "text/comma-separated-values", "application/csv"
)
def csv_serializer(ctx, value):
    # Same implementation handles all aliases
    if isinstance(value, bytes):
        return value
    return serialize_to_csv(value)  # Returns bytes
```

### Context-aware serialization

```python
@schemathesis.serializer("text/csv")
def context_aware_csv(ctx, value):
    """Use test case information to customize serialization"""
    if isinstance(value, bytes):
        return value

    # Different CSV format based on endpoint
    if "/bulk-import" in ctx.case.path:
        delimiter = '\t'  # Use tabs for bulk import
    else:
        delimiter = ','   # Use commas for regular upload
    
    return serialize_csv_with_delimiter(value, delimiter).encode('utf-8')
```

!!! info "Automatic Transport Registration"
    Serializers are automatically registered for all transport types (HTTP requests, ASGI, WSGI)

## What's Next

- **[Custom Media Types](custom-media-types.md)** - Generate raw data when there's no JSON Schema
- **[Extending Schemathesis](extending.md)** - Other customization options
- **[Serialization API Reference](../reference/python.md#serialization)**
