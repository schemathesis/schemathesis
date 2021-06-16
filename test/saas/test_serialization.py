from test.apps.openapi.schema import OpenAPIVersion

import pytest

import schemathesis
from schemathesis.runner import from_schema
from schemathesis.saas.serialization import prepare_query, serialize_event, stringify_path_parameters


@pytest.mark.operations("multiple_failures")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_serialize_event(schema_url):
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    next(events)
    next(events)
    event = serialize_event(next(events))
    assert "interactions" not in event["result"]
    assert "logs" not in event["result"]
    assert event["result"]["checks"][0]["example"]["query"] == {"id": ["0"]}


@pytest.mark.parametrize(
    "query, expected",
    (
        (None, {}),
        ({"f": 1}, {"f": ["1"]}),
        ({"f": "1"}, {"f": ["1"]}),
        ({"f": [1]}, {"f": ["1"]}),
    ),
)
def test_prepare_query(query, expected):
    assert prepare_query(query) == expected


@pytest.mark.parametrize(
    "query, expected",
    (
        (None, {}),
        ({"f": 1}, {"f": "1"}),
    ),
)
def test_stringify_path_parameters(query, expected):
    assert stringify_path_parameters(query) == expected
