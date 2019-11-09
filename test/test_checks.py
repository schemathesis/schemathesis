import pytest
import requests

from schemathesis import models
from schemathesis.runner import content_type_conformance


@pytest.fixture()
def response(request):
    response = requests.Response()
    response.headers["Content-Type"] = request.param
    return response


@pytest.fixture()
def results(request, swagger_20) -> models.TestResult:
    endpoint = models.Endpoint("/path", "GET", definition={"produces": request.param})
    return models.TestResult(endpoint, swagger_20)


@pytest.mark.parametrize(
    "response, results",
    (
        ("application/json", []),
        ("application/json", ["application/json"]),
        ("application/json;charset=utf-8", ["application/json"]),
    ),
    indirect=["response", "results"],
)
def test_content_type_conformance_valid(response, results):
    assert content_type_conformance(response, results) is None


@pytest.mark.parametrize(
    "response, results",
    (("plain/text", ["application/json"]), ("plain/text;charset=utf-8", ["application/json"])),
    indirect=["response", "results"],
)
def test_content_type_conformance_invalid(response, results):
    with pytest.raises(AssertionError, match="^Content type is not listed in 'produces' field$"):
        content_type_conformance(response, results)
