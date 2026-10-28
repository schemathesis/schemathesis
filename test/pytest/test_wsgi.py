from unittest.mock import ANY

import pytest
import requests
from flask import Flask, jsonify, redirect, request

import schemathesis
from schemathesis.core.transforms import to_wire_string
from schemathesis.specs.openapi.checks import ignored_auth
from test.utils import run_given


@pytest.mark.hypothesis_nested
def test_cookies(ctx):
    app = Flask(__name__)

    @app.route("/cookies", methods=["GET"])
    def cookies():
        return jsonify(request.cookies)

    operation = ctx.openapi.load_operation(
        "GET /cookies",
        {
            "parameters": [
                {
                    "name": "token",
                    "in": "cookie",
                    "required": True,
                    "schema": {"type": "string", "enum": ["test"]},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        response = case.call(app=app)
        assert response.status_code == 200
        assert response.json() == {"token": "test"}


@pytest.mark.hypothesis_nested
def test_login_redirect_counts_as_enforced_auth(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {"/me": {"get": {"security": [{"session": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"session": {"type": "apiKey", "in": "cookie", "name": "session"}}},
    )

    @app.route("/me", methods=["GET"])
    def me():
        if request.cookies.get("session") != "valid":
            return redirect("/login")
        return jsonify({"user": "alice"})

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

    @schema.auth()
    class Session:
        def get(self, case, context):
            return "valid"

        def set(self, case, data, context):
            case.cookies = {"session": data}

    @run_given(case=schema["/me"]["GET"].as_strategy(), max_examples=1)
    def test(case):
        assert case.call_and_validate(checks=[ignored_auth]).json() == {"user": "alice"}


def sign_request(request):
    request.headers["X-Signature"] = f"{request.method} {request.url}"
    return request


@pytest.mark.parametrize("base_url", [None, "http://api.example.com/v1"], ids=["default-host", "base-url"])
def test_session_auth_reaches_the_app(ctx, base_url):
    app = Flask(__name__)

    @app.route("/v1/items", methods=["POST"])
    @app.route("/items", methods=["POST"])
    def items():
        return jsonify(
            {
                "signature": request.headers.get("X-Signature"),
                "body": request.get_json(),
            }
        )

    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    session = requests.Session()
    session.auth = sign_request
    response = operation.Case(body={"id": 1}).call(app=app, session=session, base_url=base_url)
    url = f"{base_url or 'http://localhost'}/items"
    assert response.json() == {"signature": f"POST {url}", "body": {"id": 1}}


@pytest.mark.hypothesis_nested
def test_form_data(ctx):
    api = ctx.openapi.apps.multipart()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    strategy = schema["/api/multipart"]["POST"].as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        # converted to string in the app
        assert response.json() == {key: to_wire_string(value) for key, value in case.body.items()}


@pytest.mark.hypothesis_nested
def test_binary_body(ctx):
    # When an API operation accepts a binary input
    api = ctx.openapi.apps.upload_file()
    operation = ctx.openapi.load_operation(
        "POST /api/upload_file",
        {
            "requestBody": {
                "content": {"application/octet-stream": {"schema": {"format": "binary", "type": "string"}}}
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    strategy = operation.as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        response = case.call(app=api.wsgi_app)
        assert response.status_code == 200
        assert response.json() == {"size": ANY}

    # Then it should be sent correctly


def test_app_with_parametrize(testdir):
    # Regression - missed argument inside "wrapper" in `BaseSchema.parametrize`
    testdir.makepyfile(
        """
    import schemathesis
    from hypothesis import settings
    from test.apps.catalog.openapi.basic import success_and_failure

    api = success_and_failure()
    app = api.server
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

    called = False

    @schema.parametrize()
    @settings(max_examples=1)
    def test(case):
        global called
        called = True
        assert case.operation.schema.app is app

    def test_two():
        assert called
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=3)
