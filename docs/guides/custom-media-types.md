# Custom Media Types

Generate valid payloads for media types that Schemathesis doesn't recognize by default.

## When to use custom media types

Use custom media type strategies when your API accepts content that Schemathesis can't generate automatically:

- **Binary formats** - PDFs, images, audio files, or proprietary formats
- **Specialized text formats** - Custom configuration files, or domain-specific formats

## Quick Start: PDF File Upload

**Problem:** Your API accepts PDF uploads, but Schemathesis generates invalid binary data that fails validation.

Register a strategy that generates valid PDF headers:

```python
# pdf_strategy.py
from hypothesis import strategies as st
import schemathesis

pdf_strategy = st.sampled_from(
    [
        b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\nxref\n0 3\n0000000000 65535 f \ntrailer\n<<\n/Size 3\n/Root 10 R\n>>\nstartxref\n9\n%%EOF",
        b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\nxref\n0 3\n0000000000 65535 f \ntrailer\n<<\n/Size 3\n/Root 1 0 R\n>>\nstartxref\n9\n%%EOF",
    ]
)
# Register the strategy for PDF media type
schemathesis.openapi.media_type("application/pdf", pdf_strategy)
```

```bash
export SCHEMATHESIS_HOOKS=pdf_strategy
schemathesis run http://localhost:8000/openapi.json
```

**Result:** File upload endpoints receive valid PDF content instead of random bytes.

!!! important "Media Type Matching"
    The media type string you register must exactly match the one defined in your OpenAPI specification. For example, if your spec uses `application/pdf`, register exactly `application/pdf` - not `application/x-pdf` or any other variation. Check your OpenAPI schema's `requestBody.content` section to find the exact media type strings.

## Common Media Type Patterns

### Image files

```python
image_strategy = st.sampled_from(
    [
        # Minimal PNG
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\nIDAT\x08\x1dc\xf8\x00\x00\x00\x01\x00\x01\x00\x00\x00\x00IEND\xaeB`\x82",
        # Minimal JPEG
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xff\xd9",
    ]
)

schemathesis.openapi.media_type("image/*", image_strategy)
```

### Archives

```python
import zipfile
import io

def create_test_zip():
    """Create a minimal valid ZIP file"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        zf.writestr("test.txt", "test content")
    return buffer.getvalue()

zip_strategy = st.just(create_test_zip())
schemathesis.openapi.media_type("application/zip", zip_strategy)
```

## Wildcard Patterns

Register strategies using wildcard patterns to match multiple media types:

```python
# Register a single strategy for all image types
image_strategy = st.sampled_from([
    b"\x89PNG\r\n\x1a\n...",  # PNG
    b"\xff\xd8\xff\xe0...",   # JPEG
])
schemathesis.openapi.media_type("image/*", image_strategy)
```

This works everywhere in your OpenAPI schema:

```yaml
# Regular requestBody
requestBody:
  content:
    image/png:          # Matches registered image/*
      schema:
        type: string
        format: binary

# Multipart encoding
requestBody:
  content:
    multipart/form-data:
      schema:
        properties:
          avatar:
            type: string
            format: binary
      encoding:
        avatar:
          contentType: image/jpeg  # Matches registered image/*
```

Wildcards work bidirectionally - you can register `image/*` and use `image/png` in your schema, or register `image/png` and use `image/*` in your schema.

## Media Type Aliases

```python
# Register PDF strategy with common aliases
schemathesis.openapi.media_type(
    "application/pdf",
    pdf_strategy,
    aliases=["application/x-pdf", "application/acrobat"]
)

# Handles application/pdf, application/x-pdf, and application/acrobat
```

## Dynamic Content Generation

```python
from hypothesis import strategies as st

@st.composite
def dynamic_xml(draw):
    """Generate XML with random but valid structure"""
    tag_name = draw(st.text(
        alphabet=st.characters(
            whitelist_categories=["L"]), min_size=3, max_size=10
        )
    )
    content = draw(st.text(min_size=1, max_size=50))

    return f"<?xml version='1.0'?><{tag_name}>{content}</{tag_name}>".encode()

schemathesis.openapi.media_type("application/xml", dynamic_xml())
```

## Multipart Form Fields

When using `multipart/form-data`, you can specify custom content types for individual form fields using the `encoding` property. Schemathesis will automatically use your registered strategies for those fields:

```python
# Register strategies for specific content types
pdf_strategy = st.just(b"%PDF-1.4\n...")
xml_strategy = st.just(b"<?xml version='1.0'?><root/>")

schemathesis.openapi.media_type("application/pdf", pdf_strategy)
schemathesis.openapi.media_type("text/xml", xml_strategy)
```

```yaml
# Your OpenAPI schema
requestBody:
  content:
    multipart/form-data:
      schema:
        type: object
        properties:
          document:
            type: string
            format: binary
          metadata:
            type: string
            format: binary
      encoding:
        document:
          contentType: application/pdf    # Uses PDF strategy
        metadata:
          contentType: text/xml            # Uses XML strategy
```

The `encoding.{field}.contentType` tells Schemathesis which registered strategy to use for each form field. Fields without custom encoding use default generation.

### Multiple Content Types

You can specify multiple acceptable content types for a field. Schemathesis will randomly choose between registered strategies:

```python
# Register strategies for different image formats
schemathesis.openapi.media_type("image/png", st.just(b"\x89PNG\r\n\x1a\n..."))
schemathesis.openapi.media_type("image/jpeg", st.just(b"\xff\xd8\xff\xe0..."))
```

```yaml
encoding:
  avatar:
    contentType: "image/png, image/jpeg"  # Randomly uses PNG or JPEG
```
