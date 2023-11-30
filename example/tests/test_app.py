import pytest
from hypothesis import settings

import schemathesis

schema = schemathesis.from_uri("http://127.0.0.1:5123/openapi.json")


@pytest.fixture(scope="session")
def token():
    return "spam"


@schema.parametrize()
@settings(max_examples=25)
def test_app(case, token):
    case.headers = {"Authorization": f"Bearer {token}"}
    response = case.call()
    case.validate_response(response)
