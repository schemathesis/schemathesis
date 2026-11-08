from __future__ import annotations

import pytest
from flask import Flask, abort, got_request_exception, jsonify
from werkzeug.exceptions import HTTPException

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
