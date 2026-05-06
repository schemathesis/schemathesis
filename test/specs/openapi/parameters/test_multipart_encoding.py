import json

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.errors import SerializationNotPossible
from schemathesis.generation import GenerationMode
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.serialization import Binary
from schemathesis.transport.wsgi import WSGI_TRANSPORT


@pytest.fixture
def custom_part_serializer():
    @schemathesis.serializer("application/x-part-custom")
    def serialize_custom(ctx, value):
        return b"CUSTOM:" + json.dumps(value).encode()

    yield

    for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT, ASGI_TRANSPORT):
        transport.unregister_serializer("application/x-part-custom")


def make_operation(
    ctx,
    content: dict,
    *,
    media_type: str = "multipart/form-data",
    path: str = "/upload",
    method: str = "post",
    required: bool = True,
    responses: dict | None = None,
):
    request_body = {"content": {media_type: content}}
    if required:
        request_body["required"] = True
    schema = ctx.openapi.load_schema(
        {
            path: {
                method: {
                    "requestBody": request_body,
                    "responses": responses if responses is not None else {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
    )
    return schema[path][method.upper()]


def test_multipart_with_custom_encoding(ctx):
    xml_strategy = st.just(b"<?xml version='1.0'?><root>test</root>")
    schemathesis.openapi.media_type("text/xml", xml_strategy)

    # Given an OpenAPI schema with multipart/form-data and custom encoding
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["name", "file"],
                "properties": {
                    "name": {"type": "string"},
                    "file": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {"file": {"contentType": "text/xml"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Then the file property should use the XML strategy
        assert case.body["file"] == b"<?xml version='1.0'?><root>test</root>"
        assert isinstance(case.body["name"], str)

    test()


def test_multipart_without_custom_encoding_uses_default(ctx):
    # Given an OpenAPI schema with multipart but NO custom encoding
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["name", "file"],
                "properties": {
                    "name": {"type": "string"},
                    "file": {"type": "string", "format": "binary"},
                },
            },
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert isinstance(case.body["file"], (bytes | Binary))
        assert isinstance(case.body["name"], str)

    test()


def test_multipart_encoding_without_registered_strategy_falls_back(ctx):
    # Given custom encoding but NO registered strategy for that content type
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {"file": {"contentType": "application/unknown"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Should fall back to default strategy
        assert isinstance(case.body["file"], (bytes | Binary))

    test()


def test_multipart_multiple_properties_with_different_encodings(ctx):
    # Register strategies for different media types
    xml_strategy = st.just(b"<xml>data</xml>")
    csv_strategy = st.just(b"col1,col2\nval1,val2")
    schemathesis.openapi.media_type("text/xml", xml_strategy)
    schemathesis.openapi.media_type("text/csv", csv_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["document", "spreadsheet", "description"],
                "properties": {
                    "document": {"type": "string", "format": "binary"},
                    "spreadsheet": {"type": "string", "format": "binary"},
                    "description": {"type": "string"},
                },
            },
            "encoding": {
                "document": {"contentType": "text/xml"},
                "spreadsheet": {"contentType": "text/csv"},
            },
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert case.body["document"] == b"<xml>data</xml>"
        assert case.body["spreadsheet"] == b"col1,col2\nval1,val2"
        assert isinstance(case.body["description"], str)

    test()


def test_multipart_encoding_with_multiple_content_types(ctx):
    # Register strategies for both image types
    png_strategy = st.just(b"\x89PNG\r\n\x1a\n")
    jpeg_strategy = st.just(b"\xff\xd8\xff\xe0")
    schemathesis.openapi.media_type("image/png", png_strategy)
    schemathesis.openapi.media_type("image/jpeg", jpeg_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["image"],
                "properties": {
                    "image": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {"image": {"contentType": "image/png, image/jpeg"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Should use one of the registered strategies
        assert case.body["image"] in (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\xe0")

    test()


def test_urlencoded_form_with_encoding(ctx):
    xml_strategy = st.just(b"<data>test</data>")
    schemathesis.openapi.media_type("text/xml", xml_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["data"],
                "properties": {
                    "data": {"type": "string"},
                },
            },
            "encoding": {"data": {"contentType": "text/xml"}},
        },
        media_type="application/x-www-form-urlencoded",
        path="/submit",
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert case.body["data"] == b"<data>test</data>"

    test()


def test_multipart_optional_properties(ctx):
    xml_strategy = st.just(b"<optional/>")
    schemathesis.openapi.media_type("text/xml", xml_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "file": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {"file": {"contentType": "text/xml"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert isinstance(case.body["name"], str)
        if "file" in case.body:
            assert case.body["file"] == b"<optional/>"

    test()


def test_multipart_wildcard_matching_specific_to_wildcard(ctx):
    image_strategy = st.just(b"\x89PNG\r\n\x1a\n")
    schemathesis.openapi.media_type("image/*", image_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["avatar"],
                "properties": {
                    "avatar": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {
                "avatar": {
                    # Specific type should match wildcard
                    "contentType": "image/png"
                }
            },
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert case.body["avatar"] == b"\x89PNG\r\n\x1a\n"

    test()


def test_multipart_wildcard_matching_wildcard_to_specific(ctx):
    png_strategy = st.just(b"\x89PNG")
    jpeg_strategy = st.just(b"\xff\xd8\xff")
    schemathesis.openapi.media_type("image/png", png_strategy)
    schemathesis.openapi.media_type("image/jpeg", jpeg_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["photo"],
                "properties": {
                    "photo": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {
                "photo": {
                    # Wildcard should match registered specific types
                    "contentType": "image/*"
                }
            },
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert case.body["photo"] in (b"\x89PNG", b"\xff\xd8\xff")

    test()


def test_multipart_exact_match_preferred_over_wildcard(ctx):
    wildcard_strategy = st.just(b"WILDCARD")
    exact_strategy = st.just(b"EXACT")
    schemathesis.openapi.media_type("image/*", wildcard_strategy)
    schemathesis.openapi.media_type("image/png", exact_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["icon"],
                "properties": {
                    "icon": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {"icon": {"contentType": "image/png"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert case.body["icon"] == b"EXACT"

    test()


def test_multipart_defensive_non_string_content_type(ctx):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["data"],
                "properties": {
                    "data": {"type": "string", "format": "binary"},
                },
            },
            # Malformed encoding (contentType is not a string)
            "encoding": {"data": {"contentType": 123}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Should fall back to default strategy
        assert isinstance(case.body["data"], (bytes | Binary))

    test()


def test_nested_object_with_encoding(ctx):
    xml_strategy = st.just(b"<xml/>")
    schemathesis.openapi.media_type("text/xml", xml_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "properties": {
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                        },
                    },
                },
                "required": ["metadata"],
            },
            "encoding": {
                # This refers to top-level "file", but there isn't one
                "file": {"contentType": "text/xml"}
            },
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Should ignore the encoding since "file" is nested, not top-level
        assert "metadata" in case.body
        assert isinstance(case.body["metadata"], dict)

    test()


def test_additional_properties_with_encoding(ctx):
    xml_strategy = st.just(b"<xml/>")
    schemathesis.openapi.media_type("text/xml", xml_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["file"],
                "additionalProperties": {"type": "string"},
            },
            "encoding": {"file": {"contentType": "text/xml"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert case.body["file"] == b"<xml/>"
        for key, value in case.body.items():
            if key != "file":
                assert isinstance(value, str)

    test()


def test_empty_encoding_object(ctx):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["file"],
            },
            "encoding": {},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        assert "file" in case.body

    test()


def test_encoding_with_invalid_content_type_format(ctx):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["file"],
            },
            "encoding": {"file": {"contentType": ""}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Should handle empty string gracefully
        assert "file" in case.body

    test()


def test_multipart_comma_separated_without_custom_strategy(ctx):
    # Comma-separated content types should work even without custom strategies
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "format": "binary"},
                },
                "required": ["image"],
            },
            "encoding": {"image": {"contentType": "image/png, image/jpeg"}},
        },
    )

    content_types_seen = set()

    @given(case=operation.as_strategy())
    def test(case):
        if isinstance(case.body, dict):
            files, _ = case.operation.prepare_multipart(case.body, case.multipart_content_types)

            if files:
                for file_tuple in files:
                    name = file_tuple[0]
                    if name == "image":
                        if len(file_tuple) > 1 and isinstance(file_tuple[1], tuple):
                            if len(file_tuple[1]) == 3:
                                _, _, content_type = file_tuple[1]
                                assert content_type in ["image/png", "image/jpeg"], (
                                    f"Expected single content type, got: {content_type}"
                                )
                                content_types_seen.add(content_type)

    test()
    assert len(content_types_seen) == 2, f"Expected both content types, got: {content_types_seen}"


def test_multipart_optional_encoding_not_always_present(ctx):
    # Optional fields with custom encoding should not always be present
    xml_strategy = st.just(b"<data/>")
    schemathesis.openapi.media_type("text/xml", xml_strategy)

    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "properties": {
                    "required_field": {"type": "string"},
                    "optional_file": {"type": "string", "format": "binary"},
                },
                "required": ["required_field"],
            },
            "encoding": {"optional_file": {"contentType": "text/xml"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        # Required field must always be present
        assert "required_field" in case.body
        # Optional field may or may not be present - both are valid

    test()


def test_multipart_json_encoding_serializes_object_field(ctx):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["data"],
                "properties": {
                    "data": {
                        "type": "object",
                        "properties": {"foo": {"type": "string"}},
                        "required": ["foo"],
                    },
                    "new_cert_path": {"type": "string", "format": "binary"},
                },
            },
            "encoding": {"data": {"contentType": "application/json"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        kwargs = case.as_transport_kwargs(base_url="http://example.com")
        files = kwargs["files"]
        assert files is not None
        for name, payload in files:
            if name == "data":
                assert isinstance(payload, tuple) and len(payload) == 3, payload
                _, value, content_type = payload
                assert content_type == "application/json"
                assert isinstance(value, (str, bytes)), (
                    f"expected JSON-serialized value, got {type(value).__name__}: {value!r}"
                )
                assert isinstance(json.loads(value), dict)

    test()


def test_multipart_yaml_encoding_serializes_object_field(ctx):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["meta"],
                "properties": {
                    "meta": {
                        "type": "object",
                        "properties": {"tag": {"type": "string"}},
                        "required": ["tag"],
                    },
                },
            },
            "encoding": {"meta": {"contentType": "application/yaml"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        kwargs = case.as_transport_kwargs(base_url="http://example.com")
        files = kwargs["files"]
        assert files is not None
        for name, payload in files:
            if name == "meta":
                _, value, content_type = payload
                assert content_type == "application/yaml"
                assert isinstance(value, (str, bytes))
                parsed = yaml.safe_load(value)
                assert isinstance(parsed, dict) and "tag" in parsed

    test()


def test_multipart_custom_serializer_encoding(ctx, custom_part_serializer):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["payload"],
                "properties": {
                    "payload": {
                        "type": "object",
                        "properties": {"k": {"type": "string"}},
                        "required": ["k"],
                    },
                },
            },
            "encoding": {"payload": {"contentType": "application/x-part-custom"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        kwargs = case.as_transport_kwargs(base_url="http://example.com")
        files = kwargs["files"]
        assert files is not None
        for name, payload in files:
            if name == "payload":
                _, value, content_type = payload
                assert content_type == "application/x-part-custom"
                assert value.startswith(b"CUSTOM:")

    test()


def test_multipart_encoding_with_unknown_content_type_fails(ctx):
    operation = make_operation(
        ctx,
        {
            "schema": {
                "type": "object",
                "required": ["meta"],
                "properties": {
                    "meta": {
                        "type": "object",
                        "properties": {"tag": {"type": "string"}},
                        "required": ["tag"],
                    },
                },
            },
            "encoding": {"meta": {"contentType": "application/x-unknown-foo"}},
        },
    )

    @given(case=operation.as_strategy())
    def test(case):
        with pytest.raises(SerializationNotPossible, match="application/x-unknown-foo"):
            case.as_transport_kwargs(base_url="http://example.com")

    test()


def test_multipart_encoding_with_negative_mode(ctx):
    operation = make_operation(
        ctx,
        {
            "encoding": {"captionfile": {"contentType": "text/vtt, application/x-subrip"}},
            "schema": {"properties": {"captionfile": {"format": "binary"}}},
        },
        path="/test",
        method="put",
        required=False,
        responses={"default": {}},
    )

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    def test(case):
        # Should not raise "unhashable type: 'GeneratedValue'"
        pass

    test()
