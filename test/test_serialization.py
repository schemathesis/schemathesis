import csv
import string
from contextlib import suppress
from io import StringIO
from xml.etree import ElementTree

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis import serializers
from schemathesis.exceptions import (
    SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE,
    SerializationError,
    SerializationNotPossible,
    UnboundPrefixError,
)
from schemathesis.internal.copy import fast_deepcopy
from schemathesis.transports import RequestsTransport, WSGITransport
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
    @schemathesis.serializer("text/csv", aliases=["text/tsv"])
    class CSVSerializer:
        def as_requests(self, context, value):
            return {"data": to_csv(value)}

        def as_werkzeug(self, context, value):
            return {"data": to_csv(value)}

    assert serializers.SERIALIZERS["text/csv"] is CSVSerializer
    assert serializers.SERIALIZERS["text/tsv"] is CSVSerializer

    yield

    serializers.unregister("text/csv")
    schemathesis.serializers.unregister("text/tsv")


@pytest.fixture(params=["aiohttp", "flask"])
def api_schema(request, openapi_version):
    if request.param == "aiohttp":
        schema_url = request.getfixturevalue("schema_url")
        return schemathesis.from_uri(schema_url)
    app = request.getfixturevalue("flask_app")
    return schemathesis.from_wsgi("/schema.yaml", app=app)


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
        if case.app is not None:
            data = response.json
        else:
            data = response.json()
        assert data == case.body

    test()


def test_register_incomplete_serializer():
    # When register a new serializer without a required method
    # Then you'll have a TypeError
    with pytest.raises(TypeError, match="`CSVSerializer` is not a valid serializer."):

        @schemathesis.serializer("text/csv")
        class CSVSerializer:
            def as_requests(self, context, value):
                pass


@pytest.mark.hypothesis_nested
@pytest.mark.operations("csv_payload")
def test_no_serialization_possible(api_schema):
    # When API expects `text/csv`
    # And there is no registered serializer for this media type

    @given(case=api_schema["/csv"]["POST"].as_strategy())
    @settings(max_examples=5)
    def test(case):
        pass

    # Then there should be an error indicating this
    with pytest.raises(
        SerializationNotPossible,
        match="Schemathesis can't serialize data to any of the defined media types: text/csv",
    ):
        test()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("csv_payload")
def test_in_cli(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.parametrize("transport", (RequestsTransport(), WSGITransport(42)))
def test_serialize_yaml(open_api_3_schema_with_yaml_payload, transport):
    # See GH-1010
    # When API expects `text/yaml`
    schema = schemathesis.from_dict(open_api_3_schema_with_yaml_payload)
    schema.transport = transport

    @given(case=schema["/yaml"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then Schemathesis should generate valid YAML, not JSON with `application/json` media type
        kwargs = case.as_transport_kwargs()
        assert kwargs["headers"]["Content-Type"] == "text/yaml"
        assert kwargs["data"] == "- 42\n"

    test()


def test_serialize_any(empty_open_api_3_schema):
    # See GH-1526
    # When API expects `*/*`
    empty_open_api_3_schema["paths"] = {
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
    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/any"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then Schemathesis should generate valid data of any supported type
        assert case.as_transport_kwargs()["headers"]["Content-Type"] in serializers.SERIALIZERS

    test()


def test_serialization_not_possible_manual(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/test"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        case.media_type = "application/whatever"
        with pytest.raises(
            SerializationNotPossible, match=SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE.format(case.media_type)
        ):
            case.as_transport_kwargs()

    test()


@pytest.mark.parametrize(
    "media_type", ("text/yaml", "application/x-www-form-urlencoded", "text/plain", "multipart/form-data")
)
def test_binary_data(empty_open_api_3_schema, media_type):
    empty_open_api_3_schema["paths"] = {
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
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    operation = schema["/test"]["POST"]
    # When an explicit bytes value is passed as body (it happens with `externalValue`)
    body = b"\x92\x42"
    case = operation.make_case(body=body, media_type=media_type)
    # Then it should be used as is
    for transport in (RequestsTransport(), WSGITransport(app=None)):
        kwargs = transport.serialize_case(case)
        assert kwargs["data"] == body
        if media_type != "multipart/form-data":
            # Don't know the proper header for raw multipart content
            assert kwargs["headers"]["Content-Type"] == media_type
    # And it is OK to send it over the network
    assert_requests_call(case)


@pytest.mark.parametrize(
    "media_type, expected",
    (
        ("application/json", {"application/json"}),
        ("application/problem+json", {"application/problem+json"}),
        (
            "application/*",
            {
                "application/json",
                "application/octet-stream",
                "application/x-www-form-urlencoded",
                "application/x-yaml",
                "application/yaml",
                "application/xml",
            },
        ),
        ("*/form-data", {"multipart/form-data"}),
        ("*/*", set(serializers.SERIALIZERS)),
    ),
)
def test_get_matching_serializers(media_type, expected):
    assert set(serializers.get_matching_media_types(media_type)) == expected


@pytest.mark.parametrize(
    "path, expected",
    (
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
            b'<PrefixedAttribute smp:id="42"></PrefixedAttribute>',
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
    ),
)
def test_serialize_xml(openapi_3_schema_with_xml, path, expected):
    # When the schema contains XML payload
    schema = schemathesis.from_dict(openapi_3_schema_with_xml)

    @given(case=schema[path]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then it should be correctly serialized
        for transport in (RequestsTransport(), WSGITransport(app=None)):
            data = transport.serialize_case(case)["data"]
            assert data == expected
            # Arrays may be serialized into multiple elements without root, therefore wrapping everything and check if
            # it can be parsed.
            ElementTree.fromstring(f"<root xmlns:smp='http://example.com/schema'>{data.decode('utf8')}</root>")

    original = fast_deepcopy(schema[path]["POST"].body[0].definition)

    test()
    # And serialization does not modify the original schema
    assert schema[path]["POST"].body[0].definition == original


@pytest.mark.parametrize(
    "schema_object",
    (
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
    ),
)
def test_serialize_xml_unbound_prefix(empty_open_api_3_schema, schema_object):
    # When the schema contains an unbound prefix
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {
                    "content": {"application/xml": {"schema": {"$ref": "#/components/schemas/Main"}}},
                    "required": True,
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    empty_open_api_3_schema["components"] = {"schemas": {"Main": schema_object}}

    schema = schemathesis.from_dict(empty_open_api_3_schema)

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


@pytest.mark.parametrize("media_type", ("application/xml", "application/xml; charset=utf-8"))
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

    schema = schemathesis.from_dict(raw_schema)

    case = data.draw(schema["/test"]["POST"].as_strategy())

    # Arrays may be serialized into multiple elements without root, therefore wrapping everything and check if
    # it can be parsed.
    with suppress(SerializationError, UnboundPrefixError):
        for transport in (RequestsTransport(), WSGITransport(app=None)):
            serialized_data = transport.serialize_case(case)["data"].decode("utf8")
            ElementTree.fromstring(f"<root xmlns:smp='http://example.com/schema'>{serialized_data}</root>")


def test_xml_with_binary(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/test"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        assert case.as_transport_kwargs()["data"] == ""

    test()
