from test.apps.openapi.schema import OpenAPIVersion
from unittest.mock import ANY

import pytest

import schemathesis
from schemathesis.runner import from_schema
from schemathesis.runner.events import InternalError
from schemathesis.service.serialization import prepare_query, serialize_event, stringify_path_parameters


@pytest.mark.operations("success", "multiple_failures")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_serialize_event(schema_url):
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    next(events)
    next(events)
    event = serialize_event(next(events))
    assert "interactions" not in event["AfterExecution"]["result"]
    assert "logs" not in event["AfterExecution"]["result"]
    assert event["AfterExecution"]["result"]["checks"][0]["example"]["query"] == {"id": ["0"]}


@pytest.mark.operations("success")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_serialize_interrupted(mocker, schema_url):
    mocker.patch("schemathesis.runner.impl.solo.SingleThreadRunner._execute_impl", side_effect=KeyboardInterrupt)
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    next(events)
    assert serialize_event(next(events)) == {"Interrupted": None}


def test_serialize_internal_error():
    try:
        1 / 0
    except ArithmeticError as exc:
        event = InternalError.from_exc(exc)
        assert serialize_event(event) == {
            "InternalError": {
                "message": "An internal error happened during a test run",
                "exception_type": "builtins.ZeroDivisionError",
                "exception_with_traceback": ANY,
            }
        }


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
