import csv
from io import StringIO

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
