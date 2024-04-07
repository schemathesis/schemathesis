import sys

import pytest

AUTH_CLASS_NAME = "TokenAuth"


@pytest.fixture(params=["openapi", "graphql"])
def schema_definition(request):
    if request.param == "openapi":
        # No-op, as it is already defined
        return ""
    if request.param == "graphql":
        return """
schema = schemathesis.graphql.from_file(
'''
type Book {
  title: String
  author: Author
}

type Author {
  name: String
  books: [Book]
}

type Query {
  getBooks: [Book]
}
''')
"""


@pytest.mark.parametrize(
    "class_decorator, pre_parametrize_decorator, post_parametrize_decorator",
    (
        ("@schemathesis.auth()", "", ""),
        ("@schema.auth()", "", ""),
        ("", f"@schema.auth({AUTH_CLASS_NAME})", ""),
        ("", "", f"@schema.auth({AUTH_CLASS_NAME})"),
    ),
    ids=("global", "schema", "test-pre-parametrize", "test-post-parametrize"),
)
def test_different_scopes(
    testdir, schema_definition, class_decorator, pre_parametrize_decorator, post_parametrize_decorator
):
    testdir.make_test(
        f"""
{schema_definition}
TOKEN = "Foo"

{class_decorator}
class {AUTH_CLASS_NAME}:

    def get(self, case, context):
        return TOKEN

    def set(self, case, data, context):
        case.headers = case.headers or {{}}
        case.headers["Authorization"] = f"Bearer {{data}}"

{pre_parametrize_decorator}
@schema.parametrize()
@settings(max_examples=2)
{post_parametrize_decorator}
def test(case):
    assert case.headers is not None
    assert case.headers["Authorization"] == f"Bearer {{TOKEN}}"
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)


APP_TEST_TOKEN = "BAR"


@pytest.fixture(params=["wsgi", "asgi"])
def python_app_type(request):
    return request.param


@pytest.fixture
def python_app_auth(python_app_type):
    if python_app_type == "wsgi":
        return """
import werkzeug

@schema.auth()
class Auth:

    def get(self, case, context):
        client = werkzeug.Client(context.app)
        response = client.post("/auth/token/", json={"username": "test", "password": "pass"})
        return response.json["access_token"]

    def set(self, case, data, context):
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
"""
    if python_app_type == "asgi":
        return """
from starlette_testclient import TestClient

@schema.auth()
class Auth:

    def get(self, case, context):
        client = TestClient(context.app)
        response = client.post("/auth/token/", json={"username": "test", "password": "pass"})
        return response.json()["access_token"]

    def set(self, case, data, context):
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
"""


@pytest.fixture
def schema_with_python_app(python_app_type):
    if python_app_type == "wsgi":
        return f"""
import json
from flask import Flask, request

app = Flask("test_app")
SCHEMA_RESPONSE = json.dumps(raw_schema)

@app.route("/schema.json")
def schema():
    return SCHEMA_RESPONSE

@app.route("/auth/token/", methods=["POST"])
def token():
    if request.json == {{"username": "test", "password": "pass"}}:
        return {{"access_token": "{APP_TEST_TOKEN}"}}
    return {{"detail": "Unauthorized"}}, 401

schema = schemathesis.from_wsgi("/schema.json", app=app)"""
    if python_app_type == "asgi":
        return f"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class AuthInput(BaseModel):
    username: str
    password: str


@app.post("/auth/token/", status_code=200)
def token(data: AuthInput):
    if data.username == "test" and data.password == "pass":
        return {{"access_token": "{APP_TEST_TOKEN}"}}
    raise HTTPException(status_code=401, detail="Unauthorized")

schema = schemathesis.from_asgi("/openapi.json", app=app, force_schema_version="30")
"""


def test_python_app(testdir, schema_with_python_app, python_app_auth):
    # When there is a WSGI / ASGI app
    testdir.make_test(
        f"""
{schema_with_python_app}

{python_app_auth}

@schema.parametrize()
@settings(max_examples=2)
def test(case):
    assert case.headers is not None
    assert case.headers["Authorization"] == f"Bearer {APP_TEST_TOKEN}"
    """,
    )
    result = testdir.runpytest("-s")
    # Then there should be a way to get auth from them
    result.assert_outcomes(passed=1)


def test_from_pytest_fixture(testdir):
    # When auth is registered on a schema created via a pytest fixture
    testdir.make_test(
        f"""
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

TOKEN = "Foo"

@lazy_schema.auth()
class {AUTH_CLASS_NAME}:

    def get(self, case, context):
        return TOKEN

    def set(self, case, data, context):
        case.headers = case.headers or {{}}
        case.headers["Authorization"] = f"Bearer {{data}}"

@lazy_schema.parametrize()
@settings(max_examples=2)
def test(case):
    assert case.headers is not None
    assert case.headers["Authorization"] == f"Bearer {{TOKEN}}"
        """,
    )
    result = testdir.runpytest("-s")
    # Then there should be a way to get auth from them
    result.assert_outcomes(passed=1)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "text")
def test_requests_auth(testdir, app_schema, openapi3_base_url):
    # When the user registers auth from `requests`
    testdir.make_test(
        f"""
from requests.auth import HTTPBasicAuth

schema.base_url = "{openapi3_base_url}"
auth = HTTPBasicAuth("user", "pass")

schema.auth.set_from_requests(auth).apply_to(method="GET", path="/success")

@schema.parametrize()
@settings(max_examples=2)
def test(case):
    case_auth = case.as_transport_kwargs().get("auth")
    if case.operation.path == "/success":
        assert case_auth is auth
    if case.operation.path == "/text":
        assert case_auth is None
        """,
        schema=app_schema,
    )
    result = testdir.runpytest("-s")
    # Then auth should be present in `as_transport_kwargs` output
    result.assert_outcomes(passed=2)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success", "text")
def test_conditional(testdir, app_schema, openapi3_base_url):
    # When the user sets up multiple auths applied to different API operations
    if sys.version_info < (3, 9):
        dec1 = """
auth = schema.auth()
@auth.apply_to(method="GET", path="/text")"""
        dec2 = """
auth = schema.auth()
@auth.apply_to(method="GET", path="/success")"""
    else:
        dec1 = '@schema.auth().apply_to(method="GET", path="/text")'
        dec2 = '@schema.auth().apply_to(method="GET", path="/success")'
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"

TOKEN_1 = "ABC"

{dec1}
class TokenAuth1:
    def get(self, case, context):
        return TOKEN_1

    def set(self, case, data, context):
        case.headers = {{"Authorization": f"Bearer {{data}}"}}


TOKEN_2 = "DEF"

{dec2}
class TokenAuth2:
    def get(self, case, context):
        return TOKEN_2

    def set(self, case, data, context):
        case.headers = {{"Authorization": f"Bearer {{data}}"}}


@schema.parametrize()
@settings(max_examples=2)
def test(case):
    assert case.headers is not None
    if case.operation.path == "/text":
        expected = f"Bearer {{TOKEN_1}}"
    if case.operation.path == "/success":
        expected = f"Bearer {{TOKEN_2}}"
    assert case.headers["Authorization"] == expected
""",
        schema=app_schema,
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=2)
