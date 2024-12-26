import pytest

import schemathesis
from schemathesis.runner.events import AfterExecution, InternalError, Interrupted
from schemathesis.service.serialization import serialize_event
from test.utils import EventStream


@pytest.mark.operations("success", "multiple_failures")
@pytest.mark.openapi_version("3.0")
def test_serialize_event(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    stream = EventStream(schema).execute()
    after = stream.find(AfterExecution)
    event = serialize_event(after)
    assert "interactions" not in event["AfterExecution"]["result"]
    assert "logs" not in event["AfterExecution"]["result"]
    assert event["AfterExecution"]["result"]["checks"][0]["case"]["query"] == {"id": 0}


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_serialize_interrupted(mocker, schema_url):
    mocker.patch("schemathesis.runner.phases.unit.single_threaded", side_effect=KeyboardInterrupt)
    schema = schemathesis.openapi.from_url(schema_url)
    stream = EventStream(schema).execute()
    interrupted = stream.find(Interrupted)
    assert serialize_event(interrupted) == {"Interrupted": None}


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
