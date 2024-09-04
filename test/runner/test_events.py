import pytest

from schemathesis.models import CaseSource, Check, Status, TransitionId
from schemathesis.runner import events
from schemathesis.runner.serialization import SerializedCheck


def test_unknown_exception():
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = events.InternalError.from_exc(exc)
        assert event.message == "An internal error occurred during the test run"
        assert event.exception.strip() == "ZeroDivisionError: division by zero"


@pytest.mark.parametrize("factory_name", ("requests", "werkzeug"))
def test_serialize_history(case_factory, response_factory, factory_name):
    factory = getattr(response_factory, factory_name)
    root_case = case_factory()
    value = "A"
    root_case.source = CaseSource(
        case=case_factory(),
        response=factory(headers={"X-Example": value}),
        elapsed=1.0,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )
    check = Check(
        name="test", value=Status.failure, response=factory(headers={"X-Example": "B"}), elapsed=1.0, example=root_case
    )
    serialized = SerializedCheck.from_check(check)
    assert len(serialized.history) == 1
    assert serialized.history[0].case.extra_headers["X-Example"] == value
