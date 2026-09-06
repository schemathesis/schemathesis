import pytest

from schemathesis.specs.openapi.adapter.formdata import prepare_multipart_v3


def test_content_type_for_undefined_single_property(ctx):
    # When encoding specifies contentType for a field NOT in schema properties
    schema = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"type": "object", "properties": {}},
                                "encoding": {"field": {"contentType": "text/plain"}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
    )
    operation = schema["/upload"]["POST"]
    form_data = {"field": "value"}

    files, data = prepare_multipart_v3(operation, form_data)

    assert files == [("field", (None, "value", "text/plain"))]


def test_content_type_for_undefined_array_property(ctx):
    # When encoding specifies contentType for an array field NOT in schema properties
    schema = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"type": "object", "properties": {}},
                                "encoding": {"items": {"contentType": "image/jpeg"}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
    )
    operation = schema["/upload"]["POST"]
    form_data = {"items": ["data1", "data2"]}

    files, data = prepare_multipart_v3(operation, form_data)

    assert files == [
        ("items", (None, "data1", "image/jpeg")),
        ("items", (None, "data2", "image/jpeg")),
    ]


def make_multipart_operation(ctx, property_schema, *, version):
    schema = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"type": "object", "properties": {"file": property_schema}}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )
    return schema["/upload"]["POST"]


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

    assert prepare_multipart_v3(operation, {"file": "data"}) == ([("file", expected)], None)


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
        ("3.1.0", {"type": "string", "contentMediaType": "application/json"}, [("file", "a"), ("file", "b")]),
    ],
    ids=["format-binary", "octet-stream", "base64-encoding", "json-string"],
)
def test_filename_only_for_binary_array_parts(ctx, version, items_schema, expected):
    operation = make_multipart_operation(ctx, {"type": "array", "items": items_schema}, version=version)

    assert prepare_multipart_v3(operation, {"file": ["a", "b"]}) == (expected, None)
