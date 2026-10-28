import pytest

from schemathesis.specs.openapi.adapter.formdata import prepare_multipart_v3


@pytest.mark.parametrize(
    ("encoding", "form_data", "expected"),
    [
        ({"field": {"contentType": "text/plain"}}, {"field": "value"}, [("field", (None, "value", "text/plain"))]),
        (
            {"items": {"contentType": "image/jpeg"}},
            {"items": ["data1", "data2"]},
            [("items", (None, "data1", "image/jpeg")), ("items", (None, "data2", "image/jpeg"))],
        ),
    ],
    ids=["single", "array"],
)
def test_content_type_for_undefined_property(ctx, encoding, form_data, expected):
    # Encoding declares contentType for a field missing from the schema properties.
    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "content": {
                    "multipart/form-data": {
                        "schema": {"type": "object", "properties": {}},
                        "encoding": encoding,
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.0.0",
    )

    files, _ = prepare_multipart_v3(operation, form_data, "multipart/form-data")

    assert files == expected


def make_multipart_operation(ctx, property_schema, *, version):
    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "content": {
                    "multipart/form-data": {"schema": {"type": "object", "properties": {"file": property_schema}}}
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )
    return operation


@pytest.mark.parametrize(
    ("version", "property_schema", "expected"),
    [
        ("3.0.2", {"type": "string", "format": "binary"}, ("file", "data")),
        ("3.0.2", {"type": "string", "format": "base64"}, ("file", "data")),
        ("3.1.0", {"type": "string", "contentMediaType": "application/octet-stream"}, ("file", "data")),
        ("3.1.0", {"type": "string", "contentEncoding": "base64"}, ("file", "data")),
        (
            "3.1.0",
            {"type": "string", "contentMediaType": "application/json", "contentSchema": {"type": "object"}},
            (None, "data"),
        ),
        ("3.1.0", {"type": "string", "contentMediaType": "image/png"}, (None, "data")),
    ],
    ids=["format-binary", "format-base64", "octet-stream", "base64-encoding", "json-string", "image"],
)
def test_filename_only_for_binary_parts(ctx, version, property_schema, expected):
    operation = make_multipart_operation(ctx, property_schema, version=version)

    assert prepare_multipart_v3(operation, {"file": "data"}, "multipart/form-data") == ([("file", expected)], None)


@pytest.mark.parametrize(
    ("version", "items_schema", "expected"),
    [
        ("3.0.2", {"type": "string", "format": "binary"}, [("file", ("file", "a")), ("file", ("file", "b"))]),
        (
            "3.1.0",
            {"type": "string", "contentMediaType": "application/octet-stream"},
            [("file", ("file", "a")), ("file", ("file", "b"))],
        ),
        ("3.1.0", {"type": "string", "contentEncoding": "base64"}, [("file", ("file", "a")), ("file", ("file", "b"))]),
        (
            "3.1.0",
            {"type": "string", "contentMediaType": "application/json"},
            [("file", (None, "a")), ("file", (None, "b"))],
        ),
    ],
    ids=["format-binary", "octet-stream", "base64-encoding", "json-string"],
)
def test_filename_only_for_binary_array_parts(ctx, version, items_schema, expected):
    operation = make_multipart_operation(ctx, {"type": "array", "items": items_schema}, version=version)

    assert prepare_multipart_v3(operation, {"file": ["a", "b"]}, "multipart/form-data") == (expected, None)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("image/png, image/jpeg", ["image/png", "image/jpeg"]),
        ("image/png, ", ["image/png"]),
        (["image/png", 5, " "], ["image/png"]),
    ],
    ids=["comma-separated", "trailing-comma", "list"],
)
def test_declared_part_content_types(ctx, content_type, expected):
    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "content": {
                    "multipart/form-data": {
                        "schema": {"type": "object", "properties": {"file": {"type": "string"}}},
                        "encoding": {"file": {"contentType": content_type}},
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert [body.get_property_content_types("file") for body in operation.body] == [expected]
