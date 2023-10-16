import io
from datetime import timedelta

import pytest
import requests
from urllib3 import HTTPResponse

from schemathesis.models import Case, CaseSource, Check, Status
from schemathesis.runner import events
from schemathesis.runner.serialization import SerializedCheck
from schemathesis.utils import WSGIResponse


def test_unknown_exception():
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = events.InternalError.from_exc(exc)
        assert event.message == "An internal error occurred during the test run"
        assert event.exception.strip() == "ZeroDivisionError: division by zero"


@pytest.fixture
def case_factory(swagger_20):
    def factory():
        return Case(operation=swagger_20["/users"]["GET"])

    return factory


@pytest.fixture(params=[requests.Response, WSGIResponse])
def response_factory(request, mocker):
    def factory(headers):
        response = mocker.create_autospec(request.param)
        response.status_code = 500
        response.reason = "Internal Server Error"
        response.encoding = "utf-8"
        response.elapsed = timedelta(1.0)
        response.headers = {}
        response.response = []
        response.raw = HTTPResponse(body=io.BytesIO(b""), status=500, headers={})
        response.request = requests.PreparedRequest()
        response.request.prepare(method="POST", url="http://127.0.0.1", headers=headers)
        return response

    return factory


def test_serialize_history(case_factory, response_factory):
    root_case = case_factory()
    value = "A"
    root_case.source = CaseSource(case=case_factory(), response=response_factory({"X-Example": value}), elapsed=1.0)
    check = Check(
        name="test", value=Status.failure, response=response_factory({"X-Example": "B"}), elapsed=1.0, example=root_case
    )
    serialized = SerializedCheck.from_check(check)
    assert len(serialized.history) == 1
    assert serialized.history[0].case.extra_headers["X-Example"] == value
