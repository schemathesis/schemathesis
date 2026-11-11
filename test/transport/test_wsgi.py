from __future__ import annotations

from unittest.mock import ANY

import pytest
import requests
from flask import Flask, Response, abort, got_request_exception, jsonify, request
from werkzeug.exceptions import HTTPException

import schemathesis
from schemathesis.core.transforms import to_wire_string
from test.utils import run_given


@pytest.mark.hypothesis_nested
def test_wsgi_reraises_server_exception(ctx):
    app = Flask(__name__)

    @app.route("/api/crash", methods=["GET"])
    def crash():
        raise ValueError("something broke")

    operation = ctx.openapi.load_operation("GET /api/crash", {"responses": {"200": {"description": "OK"}}})
    strategy = operation.as_strategy()

    @run_given(case=strategy, max_examples=1)
    def test(case):
        with pytest.raises(ValueError, match="something broke"):
            case.call(app=app)


def test_wsgi_server_exception_keeps_its_own_notes(ctx):
    app = Flask(__name__)

    @app.route("/api/crash", methods=["GET"])
    def crash():
        error = ValueError("something broke")
        error.add_note("Raised by the app")
        raise error

    schema = ctx.openapi.load_schema({"/api/crash": {"get": {"responses": {"200": {"description": "OK"}}}}})
    with pytest.raises(ValueError, match="something broke") as exc_info:
        schema["/api/crash"]["GET"].Case().call(app=app)
    assert exc_info.value.__notes__ == [
        "Raised by the app",
        "\nReproduce with:\n\n    curl -X GET http://localhost/api/crash",
    ]


def test_wsgi_response_decodes_declared_charset(ctx):
    app = Flask(__name__)

    @app.route("/api/text", methods=["GET"])
    def text():
        return Response("café".encode("latin-1"), content_type="text/plain; charset=latin-1")

    operation = ctx.openapi.load_operation("GET /api/text", {"responses": {"200": {"description": "OK"}}})
    response = operation.Case().call(app=app)

    assert (response.message, response.encoding, response.text) == ("OK", "latin-1", "café")


def test_wsgi_returns_response_for_handled_http_exception(ctx):
    # Some extensions emit the exception signal even for errors they already turned into a response
    app = Flask(__name__)

    @app.route("/api/users", methods=["GET"])
    def users():
        abort(404, "No such user")

    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        got_request_exception.send(app, exception=error)
        return jsonify({"message": error.description}), error.code

    operation = ctx.openapi.load_operation("GET /api/users", {"responses": {"404": {"description": "Missing"}}})
    response = operation.Case().call(app=app)

    assert (response.status_code, response.json()) == (404, {"message": "No such user"})


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
