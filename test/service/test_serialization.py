import pytest

import schemathesis
from schemathesis.runner import from_schema
from schemathesis.runner.events import InternalError, Interrupted
from schemathesis.runner.serialization import prepare_query, stringify_path_parameters
from schemathesis.service.serialization import serialize_event


@pytest.mark.operations("success", "multiple_failures")
@pytest.mark.openapi_version("3.0")
def test_serialize_event(schema_url):
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    next(events)
    next(events)
    next(events)
    next(events)
    next(events)
    next(events)
    event = serialize_event(next(events))
    assert "interactions" not in event["AfterExecution"]["result"]
    assert "logs" not in event["AfterExecution"]["result"]
    assert event["AfterExecution"]["result"]["checks"][0]["example"]["query"] == {"id": ["0"]}


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_serialize_interrupted(mocker, schema_url):
    mocker.patch("schemathesis.runner.impl.solo.SingleThreadRunner._execute_impl", side_effect=KeyboardInterrupt)
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    next(events)
    next(events)
    next(events)
    next(events)
    next(events)
    assert serialize_event(next(events)) == {"Interrupted": None}


def test_serialize_internal_error():
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = InternalError.from_exc(exc)
        assert serialize_event(event) == {
            "InternalError": {
                "type": event.type.value,
                "subtype": None,
                "title": event.title,
                "message": event.message,
                "extras": event.extras,
                "exception_type": event.exception_type,
                "exception": event.exception,
                "exception_with_traceback": event.exception_with_traceback,
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


@pytest.mark.parametrize(
    "serializer, expected", ((None, "GET /api/success"), (lambda e: {"verbose_name": "Bar"}, "Bar"))
)
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_explicit_serialization(serializer, expected, schema_url):
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    next(events)
    next(events)
    next(events)
    next(events)
    next(events)
    event = next(events)
    assert serialize_event(event, on_before_execution=serializer)["BeforeExecution"]["verbose_name"] == expected


@pytest.mark.operations("success")
@pytest.mark.openapi_version("2.0")
def test_extra_values(schema_url):
    schema = schemathesis.from_uri(schema_url)
    events = from_schema(schema).execute()
    event = next(events)
    value = "localhost"
    assert serialize_event(event, extra={"schema": {"host": value}})["Initialized"]["schema"]["host"] == value


def test_extra_values_with_interrupted():
    value = {"something": 42}
    assert serialize_event(Interrupted(), extra=value)["Interrupted"] == value
