import pytest
from hypothesis import settings

import schemathesis

schema = schemathesis.from_uri("http://127.0.0.1:5000/api/openapi.json")


@pytest.fixture
def token():
    return "spam"


@schema.parametrize(operation_id="app.views.create_booking")
@settings(max_examples=25)
def test_app(case, token):
    case.headers = {"Authorization": f"Bearer {token}"}
    response = case.call()
    case.validate_response(response)


TestStateful = schema.as_state_machine().TestCase
