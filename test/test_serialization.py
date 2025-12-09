import csv
import dataclasses
import json
import platform
import re
import string
from contextlib import suppress
from io import StringIO
from xml.etree import ElementTree

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.errors import (
    SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE,
    SerializationError,
    SerializationNotPossible,
)
from schemathesis.core.transforms import deepclone
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT, RequestsTransport
from schemathesis.transport.serialization import Binary
from schemathesis.transport.wsgi import WSGI_TRANSPORT, WSGITransport
from test.utils import assert_requests_call


def to_csv(data):
    if not data:
        return ""
    output = StringIO()
    field_names = sorted(data[0].keys())
    writer = csv.DictWriter(output, field_names)
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()


@pytest.fixture
def csv_serializer():
    @schemathesis.serializer("text/csv")
    def serialize_csv(ctx, value):
        return to_csv(value)

    yield

    for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT):
        transport.unregister_serializer("text/csv", "text/tsv")


@pytest.fixture(params=["aiohttp", "flask"])
def api_schema(request, openapi_version):
    if request.param == "aiohttp":
        schema_url = request.getfixturevalue("schema_url")
        return schemathesis.openapi.from_url(schema_url)
    app = request.getfixturevalue("flask_app")
    return schemathesis.openapi.from_wsgi("/schema.yaml", app=app)


@pytest.mark.hypothesis_nested
@pytest.mark.operations("csv_payload")
@pytest.mark.usefixtures("csv_serializer")
def test_text_csv(api_schema):
    # When API expects `text/csv`
    # And the user registers a custom serializer for it

    @given(case=api_schema["/csv"]["POST"].as_strategy())
    @settings(max_examples=5, deadline=None)
    def test(case):
        # Then this serializer should be used
        response = case.call_and_validate()
        # And data should be successfully sent to the API as CSV
        assert response.json() == case.body

    test()


@pytest.fixture
def tsv_schema(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    definition = schema.raw_schema["paths"]["/csv"]["post"]
    if "consumes" in definition:
        definition["consumes"] = ["text/tsv"]
    else:
        definition["requestBody"]["content"]["text/tsv"] = definition["requestBody"]["content"].pop("text/csv")
    return schema


@pytest.mark.hypothesis_nested
@pytest.mark.operations("csv_payload")
def test_no_serialization_possible(tsv_schema):
    # When API expects `text/tsv`
    # And there is no registered serializer for this media type

    @given(case=tsv_schema["/csv"]["POST"].as_strategy())
    @settings(max_examples=5)
    def test(case):
        pass

    # Then there should be an error indicating this
    with pytest.raises(
        SerializationNotPossible,
        match="No supported serializers for media types: text/tsv",
    ):
        test()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("csv_payload")
def test_in_cli(ctx, cli, tsv_schema, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(tsv_schema.raw_schema["paths"])
    assert cli.run(str(schema_path), f"--url={openapi3_base_url}") == snapshot_cli


@pytest.mark.parametrize("transport", [RequestsTransport, WSGITransport])
def test_serialize_yaml(open_api_3_schema_with_yaml_payload, transport):
    # See GH-1010
    # When API expects `text/yaml`
    schema = schemathesis.openapi.from_dict(open_api_3_schema_with_yaml_payload)

    if transport is WSGITransport:
        schema.app = 42

    @given(case=schema["/yaml"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then Schemathesis should generate valid YAML, not JSON with `application/json` media type
        kwargs = case.as_transport_kwargs()
        assert kwargs["headers"]["Content-Type"] == "text/yaml"
        assert kwargs["data"] == "- 42\n"

    test()


def test_serialize_any(ctx):
    # See GH-1526
    # When API expects `*/*`
    schema = ctx.openapi.build_schema(
        {
            "/any": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"*/*": {"schema": {"type": "array"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/any"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then Schemathesis should generate valid data of any supported type
        assert case.as_transport_kwargs()["headers"]["Content-Type"] in REQUESTS_TRANSPORT._serializers

    test()


def test_serialization_not_possible_manual(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "integer"},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/test"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        case.media_type = "application/whatever"
        with pytest.raises(
            SerializationNotPossible,
            match=re.escape(SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE.format(case.media_type)),
        ):
            case.as_transport_kwargs()

    test()


@pytest.mark.parametrize(
    "media_type", ["text/yaml", "application/x-www-form-urlencoded", "text/plain", "multipart/form-data"]
)
def test_binary_data(ctx, media_type):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            media_type: {
                                "schema": {},
                                "examples": {"answer": {"externalValue": "http://127.0.0.1:1/answer.json"}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/test"]["POST"]
    # When an explicit bytes value is passed as body (it happens with `externalValue`)
    body = b"\x92\x42"
    case = operation.Case(body=body, media_type=media_type)
    # Then it should be used as is
    for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT):
        kwargs = transport.serialize_case(case)
        assert kwargs["data"] == body
        if media_type != "multipart/form-data":
            # Don't know the proper header for raw multipart content
            assert kwargs["headers"]["Content-Type"] == media_type
    # And it is OK to send it over the network
    assert_requests_call(case)


def test_unknown_multipart_fields_openapi3(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "data": {"type": "string", "format": "binary"},
                                        "note": {"type": "string"},
                                    },
                                    "required": ["data", "note"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/test"]["POST"]
    case = operation.Case(
        body={"data": b"\x92\x42", "note": "foo", "unknown": "seen"}, media_type="multipart/form-data"
    )
    serialized = REQUESTS_TRANSPORT.serialize_case(case)
    assert serialized["files"] == [
        ("data", b"\x92B"),
        ("note", (None, "foo")),
        ("unknown", (None, "seen")),
    ]


def test_unknown_multipart_fields_openapi2(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "data": {"type": "string", "format": "binary"},
                                    "note": {"type": "string"},
                                },
                                "required": ["data", "note"],
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
        version="2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/test"]["POST"]
    case = operation.Case(
        body={"data": b"\x92\x42", "note": "foo", "unknown": "seen"}, media_type="multipart/form-data"
    )
    serialized = REQUESTS_TRANSPORT.serialize_case(case)
    assert serialized["files"] == [
        ("data", b"\x92B"),
        ("note", "foo"),
        ("unknown", "seen"),
    ]


@pytest.mark.filterwarnings("error")
def test_multipart_examples_serialization(ctx, cli, openapi3_base_url, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "example": {"key": {}},
                                "schema": {"title": "Test"},
                            }
                        }
                    }
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--checks=response_schema_conformance") == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires a more complex test setup")
def test_multipart_with_references(ctx):
    # See GH-2776
    paths = ctx.openapi.write_schema(
        {"Test": {"post": {"requestBody": {"$ref": "#/components/requestBodies/Test"}}}},
        components={
            "requestBodies": {
                "Test": {
                    "content": {
                        "multipart/form-data": {"schema": {"type": "object"}},
                    },
                },
            },
        },
        filename="paths",
    )

    schema = ctx.openapi.write_schema(
        {
            "/test": {"$ref": f"{paths}#/paths/Test"},
        }
    )
    schema = schemathesis.openapi.from_path(schema)
    operation = schema["/test"]["POST"]
    case = operation.Case(body={}, media_type="multipart/form-data")
    serialized = REQUESTS_TRANSPORT.serialize_case(case)
    assert serialized["files"] is None


TRANSPORT = RequestsTransport()


@TRANSPORT.serializer(
    "application/json",
    "multipart/form-data",
    "application/problem+json",
    "application/octet-stream",
    "application/x-www-form-urlencoded",
    "application/x-yaml",
    "application/yaml",
    "application/xml",
)
def foo(ctx, value):
    pass


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("application/json", {"application/json"}),
        ("application/problem+json", {"application/problem+json"}),
        (
            "application/*",
            {
                "application/json",
                "application/octet-stream",
                "application/problem+json",
                "application/x-www-form-urlencoded",
                "application/x-yaml",
                "application/yaml",
                "application/xml",
            },
        ),
        ("*/form-data", {"multipart/form-data"}),
        ("*/*", set(TRANSPORT._serializers)),
    ],
)
def test_get_matching_serializers(media_type, expected):
    assert {media_type for media_type, _ in TRANSPORT.get_matching_media_types(media_type)} == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/root-name", b"<data><id>42</id></data>"),
        ("/auto-name", b"<AutoName><id>42</id></AutoName>"),
        ("/explicit-name", b"<CustomName><id>42</id></CustomName>"),
        ("/renamed-property", b"<RenamedProperty><renamed-id>42</renamed-id></RenamedProperty>"),
        ("/property-attribute", b'<PropertyAsAttribute id="42"></PropertyAsAttribute>'),
        ("/simple-array", b"<SimpleArray>42</SimpleArray><SimpleArray>42</SimpleArray>"),
        (
            "/wrapped-array",
            b"<WrappedArray><WrappedArray>42</WrappedArray><WrappedArray>42</WrappedArray></WrappedArray>",
        ),
        (
            "/array-with-renaming",
            b"<items-array><item>42</item><item>42</item></items-array>",
        ),
        (
            "/object-in-array",
            b"<items><item><item-id>42</item-id></item><item><item-id>42</item-id></item></items>",
        ),
        (
            "/array-in-object",
            b"<items-object><items-array><id>42</id><id>42</id></items-array></items-object>",
        ),
        (
            "/prefixed-object",
            b"<smp:PrefixedObject><id>42</id></smp:PrefixedObject>",
        ),
        (
            "/prefixed-array",
            b'<smp:PrefixedArray xmlns:smp="http://example.com/schema">42</smp:PrefixedArray>'
            b'<smp:PrefixedArray xmlns:smp="http://example.com/schema">42</smp:PrefixedArray>',
        ),
        (
            "/prefixed-attribute",
            b'<PrefixedAttribute xmlns:smp="http://example.com/schema" smp:id="42"></PrefixedAttribute>',
        ),
        (
            "/namespaced-object",
            b'<NamespacedObject xmlns="http://example.com/schema"><id>42</id></NamespacedObject>',
        ),
        (
            "/namespaced-array",
            b'<NamespacedArray xmlns="http://example.com/schema">42</NamespacedArray>'
            b'<NamespacedArray xmlns="http://example.com/schema">42</NamespacedArray>',
        ),
        (
            "/namespaced-wrapped-array",
            b'<NamespacedWrappedArray xmlns="http://example.com/schema">'
            b"<NamespacedWrappedArray>42</NamespacedWrappedArray>"
            b"<NamespacedWrappedArray>42</NamespacedWrappedArray>"
            b"</NamespacedWrappedArray>",
        ),
        (
            "/namespaced-prefixed-object",
            b'<smp:NamespacedPrefixedObject xmlns:smp="http://example.com/schema">'
            b"<id>42</id>"
            b"</smp:NamespacedPrefixedObject>",
        ),
        (
            "/namespaced-prefixed-array",
            b'<smp:NamespacedPrefixedArray xmlns:smp="http://example.com/schema">42</smp:NamespacedPrefixedArray>'
            b'<smp:NamespacedPrefixedArray xmlns:smp="http://example.com/schema">42</smp:NamespacedPrefixedArray>',
        ),
        (
            "/namespaced-prefixed-wrapped-array",
            b'<smp:NamespacedPrefixedWrappedArray xmlns:smp="http://example.com/schema">'
            b"<smp:NamespacedPrefixedWrappedArray>42</smp:NamespacedPrefixedWrappedArray>"
            b"<smp:NamespacedPrefixedWrappedArray>42</smp:NamespacedPrefixedWrappedArray>"
            b"</smp:NamespacedPrefixedWrappedArray>",
        ),
    ],
)
def test_serialize_xml(openapi_3_schema_with_xml, path, expected):
    # When the schema contains XML payload
    schema = schemathesis.openapi.from_dict(openapi_3_schema_with_xml)

    @given(case=schema[path]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then it should be correctly serialized
        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT):
            data = transport.serialize_case(case)["data"]
            assert data == expected
            # Arrays may be serialized into multiple elements without root, therefore wrapping everything and check if
            # it can be parsed.
            ElementTree.fromstring(f"<root xmlns:smp='http://example.com/schema'>{data.decode('utf8')}</root>")

    original = deepclone(schema[path]["POST"].body[0].definition)

    test()
    # And serialization does not modify the original schema
    assert schema[path]["POST"].body[0].definition == original


@pytest.mark.parametrize(
    "schema_object",
    [
        {
            "type": "object",
            "properties": {"id": {"enum": [42], "xml": {"prefix": "smp"}}},
            "additionalProperties": False,
            "required": ["id"],
        },
        {
            "type": "array",
            "items": {"enum": [42], "xml": {"prefix": "smp"}},
            "minItems": 2,
            "maxItems": 2,
            "xml": {"wrapped": True},
        },
        {"type": "integer", "xml": {"prefix": "smp"}},
    ],
)
def test_serialize_xml_unbound_prefix(ctx, schema_object):
    # When the schema contains an unbound prefix
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {"application/xml": {"schema": {"$ref": "#/components/schemas/Main"}}},
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={"schemas": {"Main": schema_object}},
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/test"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then it should be an error during serialization
        with pytest.raises(SerializationError, match="Unbound prefix: `smp`"):
            case.as_transport_kwargs()

    test()


SIMPLE_TEXT_STRATEGY = st.text(min_size=1, alphabet=st.sampled_from(string.ascii_letters))
XML_OBJECT_STRATEGY = st.fixed_dictionaries(
    {},
    optional={
        "name": SIMPLE_TEXT_STRATEGY,
        "namespace": SIMPLE_TEXT_STRATEGY,
        "prefix": SIMPLE_TEXT_STRATEGY,
        "attribute": st.booleans(),
        "wrapped": st.booleans(),
    },
)
PRIMITIVE_SCHEMA_STRATEGY = (
    st.fixed_dictionaries({"type": st.just("integer")}, optional={"xml": XML_OBJECT_STRATEGY})
    | st.fixed_dictionaries({"type": st.just("string")}, optional={"xml": XML_OBJECT_STRATEGY})
    | st.fixed_dictionaries({"type": st.just("boolean")}, optional={"xml": XML_OBJECT_STRATEGY})
)
SCHEMA_OBJECT_STRATEGY = st.deferred(
    lambda: st.fixed_dictionaries(
        {"type": st.just("object")},
        optional={
            "properties": st.dictionaries(SIMPLE_TEXT_STRATEGY, SCHEMA_OBJECT_STRATEGY | PRIMITIVE_SCHEMA_STRATEGY)
        },
    )
    | st.fixed_dictionaries({"type": st.just("array"), "items": SCHEMA_OBJECT_STRATEGY})
)


@pytest.mark.parametrize("media_type", ["application/xml", "application/xml; charset=utf-8"])
@given(data=st.data(), schema_object=SCHEMA_OBJECT_STRATEGY)
@settings(suppress_health_check=list(HealthCheck), deadline=None, max_examples=25, phases=[Phase.generate])
def test_serialize_xml_hypothesis(data, schema_object, media_type):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {media_type: {"schema": {"$ref": "#/components/schemas/Main"}}},
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {"Main": schema_object}},
    }

    schema = schemathesis.openapi.from_dict(raw_schema)

    case = data.draw(schema["/test"]["POST"].as_strategy())

    # Arrays may be serialized into multiple elements without root, therefore wrapping everything and check if
    # it can be parsed.
    with suppress(SerializationError):
        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT):
            serialized_data = transport.serialize_case(case)["data"].decode("utf8")
            ElementTree.fromstring(f"<root xmlns:smp='http://example.com/schema'>{serialized_data}</root>")


def test_xml_with_binary(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {"application/xml": {"schema": {"type": "string", "format": "file"}}},
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/test"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        assert isinstance(case.as_transport_kwargs()["data"], str)

    test()


def test_duplicate_xml_attributes(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "prop1": {
                                            "type": "integer",
                                            "xml": {"namespace": "foo", "name": "attr", "attribute": True},
                                        },
                                        "prop2": {
                                            "type": "integer",
                                            "xml": {"namespace": "foo", "name": "attr", "attribute": True},
                                        },
                                    },
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/test"]["POST"].Case(body={"prop1": 1, "prop2": 2})

    serialized_data = case.as_transport_kwargs()["data"].decode("utf8")
    ElementTree.fromstring(serialized_data)


def test_xml_with_referenced_property_schema(ctx):
    # When a property references a subschema that contains XML configuration
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "$ref": "#/components/schemas/Main",
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Main": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "$ref": "#/components/schemas/IdField",
                        }
                    },
                    "xml": {"name": "Root"},
                },
                "IdField": {"type": "integer", "xml": {"name": "custom-id", "attribute": True}},
            }
        },
    )

    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/test"]["POST"].Case(body={"id": 42})

    # Then the XML should use the configuration from the referenced schema
    data = REQUESTS_TRANSPORT.serialize_case(case)["data"]
    assert data == b'<Root custom-id="42"></Root>'


def test_xml_with_referenced_array_items(ctx):
    # When array items reference a subschema with XML configuration
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "$ref": "#/components/schemas/ItemList",
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "ItemList": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Item"},
                    "xml": {"name": "items", "wrapped": True},
                },
                "Item": {"type": "integer", "xml": {"name": "item"}},
            }
        },
    )

    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/test"]["POST"].Case(body=[42, 43])

    # Then the XML should use the referenced item configuration
    data = REQUESTS_TRANSPORT.serialize_case(case)["data"]
    assert data == b"<items><item>42</item><item>43</item></items>"


def test_xml_with_nested_schema_references(ctx):
    # When schemas reference other schemas that also contain references with XML config
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "$ref": "#/components/schemas/Container",
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Container": {
                    "type": "object",
                    "properties": {"user": {"$ref": "#/components/schemas/User"}},
                    "xml": {"name": "container"},
                },
                "User": {
                    "type": "object",
                    "properties": {"profile": {"$ref": "#/components/schemas/Profile"}},
                    "xml": {"name": "user-data"},
                },
                "Profile": {"type": "string", "xml": {"name": "user-profile", "attribute": True}},
            }
        },
    )

    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/test"]["POST"].Case(body={"user": {"profile": "admin"}})

    # Then XML should resolve the entire reference chain correctly
    data = REQUESTS_TRANSPORT.serialize_case(case)["data"]
    assert data == b'<container><user-data user-profile="admin"></user-data></container>'


def test_xml_root_tag_from_reference_openapi2(ctx):
    # When OpenAPI 2.0 schema references a definition for the request body
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "consumes": [
                        "application/xml",
                    ],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {"$ref": "#/definitions/UserProfile"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={
            "UserProfile": {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
        },
        version="2.0",
    )

    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/test"]["POST"].Case(body={"name": "John", "age": 30}, media_type="application/xml")

    # Then the root XML tag should be derived from the reference name "UserProfile"
    data = REQUESTS_TRANSPORT.serialize_case(case)["data"]
    assert data == b"<UserProfile><name>John</name><age>30</age></UserProfile>"


@pytest.mark.parametrize(
    "target,source,body,expected_content",
    [
        ("application/custom+yaml", "application/yaml", {"key": "value"}, "key: value"),
        ("application/x-yaml-custom", "text/yaml", {"foo": "bar"}, "foo: bar"),
        ("text/vnd.custom.yaml", "application/yaml", {"name": "test"}, "name: test"),
    ],
)
def test_serializer_alias_single(ctx, target, source, body, expected_content):
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {target: {"schema": {"type": "object"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schemathesis.serializer.alias(target, source)

    try:
        schema = schemathesis.openapi.from_dict(raw_schema)
        case = schema["/test"]["POST"].Case(body=body, media_type=target)

        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT, ASGI_TRANSPORT):
            data = transport.serialize_case(case)["data"]
            data_str = data.decode() if isinstance(data, bytes) else data
            assert expected_content in data_str
    finally:
        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT, ASGI_TRANSPORT):
            transport.unregister_serializer(target)


def test_serializer_alias_multiple_targets(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/x-custom-yaml": {"schema": {"type": "object"}},
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schemathesis.serializer.alias(["application/x-custom-yaml", "text/vnd.yaml.custom"], "application/yaml")

    try:
        schema = schemathesis.openapi.from_dict(raw_schema)

        for media_type in ["application/x-custom-yaml", "text/vnd.yaml.custom"]:
            case = schema["/test"]["POST"].Case(body={"id": 42}, media_type=media_type)
            for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT, ASGI_TRANSPORT):
                data = transport.serialize_case(case)["data"]
                data_str = data.decode() if isinstance(data, bytes) else data
                assert "id" in data_str and "42" in data_str
    finally:
        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT, ASGI_TRANSPORT):
            transport.unregister_serializer("application/x-custom-yaml", "text/vnd.yaml.custom")


@pytest.mark.parametrize(
    "target,source,expected_error",
    [
        (
            "application/custom",
            "application/nonexistent",
            "No serializer found for media type: application/nonexistent",
        ),
        ("application/custom", "", "Source media type cannot be empty"),
        ("", "application/json", "Target media type cannot be empty"),
    ],
)
def test_serializer_alias_validation(target, source, expected_error):
    with pytest.raises(ValueError, match=re.escape(expected_error)):
        schemathesis.serializer.alias(target, source)


def test_serializer_alias_empty_target_in_list():
    with pytest.raises(ValueError, match="Target media type cannot be empty"):
        schemathesis.serializer.alias(["application/custom", ""], "application/json")


def test_binary_not_a_dataclass():
    # Binary should not be a dataclass to prevent `dataclasses.asdict()` from
    # expanding it and exposing raw bytes that break JSON serialization.
    # This is important for compatibility with tools like Hypofuzz.
    assert not dataclasses.is_dataclass(Binary(b"test"))

    # Should be JSON serializable when used with dataclasses.asdict on a container
    @dataclasses.dataclass
    class Container:
        value: Binary

    container = Container(value=Binary(b"test data"))
    # asdict should not expose raw bytes
    as_dict = dataclasses.asdict(container)
    json.dumps(as_dict)
