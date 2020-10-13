from test.apps._fastapi.app import app

import schemathesis
from schemathesis import Case
from schemathesis.constants import USER_AGENT

schema = schemathesis.from_dict(app.openapi())


@schema.parametrize()
def test_basic_curl_gen(case: Case):
    command = case.as_curl_command()
    assert command == f"curl -X GET -H 'User-Agent: {USER_AGENT}' http://localhost/users"
