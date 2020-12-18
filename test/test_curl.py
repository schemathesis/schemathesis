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
