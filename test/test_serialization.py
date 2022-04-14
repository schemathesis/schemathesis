import csv
from io import StringIO
from test.utils import assert_requests_call

import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.exceptions import SerializationNotPossible


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
    @schemathesis.serializers.register("text/csv", aliases=["text/tsv"])
    class CSVSerializer:
        def as_requests(self, context, value):
            return {"data": to_csv(value)}

        def as_werkzeug(self, context, value):
            return {"data": to_csv(value)}

    assert schemathesis.serializers.SERIALIZERS["text/csv"] is CSVSerializer
    assert schemathesis.serializers.SERIALIZERS["text/tsv"] is CSVSerializer

    yield

    schemathesis.serializers.unregister("text/csv")


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
    @settings(max_examples=5)
    def test(case):
        if case.app is not None:
            response = case.call_wsgi()
        else:
            response = case.call()
        # Then this serializer should be used
        case.validate_response(response)
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

        @schemathesis.serializers.register("text/csv")
        class CSVSerializer:
            def as_requests(self, context, value):
                return {}


@pytest.mark.hypothesis_nested
@pytest.mark.operations("csv_payload")
def test_no_serialization_possible(api_schema):
    # When API expects `text/csv`
    # And there is no registered serializer for this media type

    @given(case=api_schema["/csv"]["POST"].as_strategy())
    @settings(max_examples=5)
    def test(case):
        # Then there should be an error indicating this
        with pytest.raises(
            SerializationNotPossible,
            match="Schemathesis can't serialize data to any of the defined media types: text/csv",
        ):
            if case.app is not None:
                case.call_wsgi()
            else:
                case.call()

    test()


@pytest.mark.parametrize("method", ("as_requests_kwargs", "as_werkzeug_kwargs"))
def test_serialize_yaml(open_api_3_schema_with_yaml_payload, method):
    # See GH-1010
    # When API expects `text/yaml`
    schema = schemathesis.from_dict(open_api_3_schema_with_yaml_payload)

    @given(case=schema["/yaml"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then Schemathesis should generate valid YAML, not JSON with `application/json` media type
        kwargs = getattr(case, method)()
        assert kwargs["headers"]["Content-Type"] == "text/yaml"
        assert kwargs["data"] == "- 42\n"

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
    requests_kwargs = case.as_requests_kwargs()
    assert requests_kwargs["data"] == body
    werkzeug_kwargs = case.as_werkzeug_kwargs()
    assert werkzeug_kwargs["data"] == body
    if media_type != "multipart/form-data":
        # Don't know the proper header for raw multipart content
        assert requests_kwargs["headers"]["Content-Type"] == media_type
        assert werkzeug_kwargs["headers"]["Content-Type"] == media_type
    # And it is OK to send it over the network
    assert_requests_call(case)
