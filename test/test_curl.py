from test.apps._fastapi.app import app

import pytest

import schemathesis
from schemathesis import Case
from schemathesis.constants import USER_AGENT

schema = schemathesis.from_dict(app.openapi())


@pytest.mark.parametrize("headers", (None, {"X-Key": "42"}))
@schema.parametrize()
def test_as_curl_command(case: Case, headers):
    command = case.as_curl_command(headers)
    expected_headers = "" if not headers else " ".join(f" -H '{name}: {value}'" for name, value in headers.items())
    assert command == f"curl -X GET -H 'User-Agent: {USER_AGENT}'{expected_headers} http://localhost/users"


def test_non_utf_8_body():
    case = Case(operation=schema["/users"]["GET"], body=b"42\xff", media_type="application/octet-stream")
    command = case.as_curl_command()
    assert (
        command == "curl -X GET -H 'Content-Length: 3' -H 'Content-Type: application/octet-stream' "
        f"-H 'User-Agent: {USER_AGENT}' -d '42�' http://localhost/users"
    )
