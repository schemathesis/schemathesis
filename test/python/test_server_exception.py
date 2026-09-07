from __future__ import annotations

import pytest
from flask import Flask, abort, got_request_exception, jsonify
from hypothesis import HealthCheck, given, settings
from werkzeug.exceptions import HTTPException


@pytest.mark.hypothesis_nested
def test_wsgi_reraises_server_exception(ctx):
    app = Flask(__name__)

    @app.route("/api/crash", methods=["GET"])
    def crash():
        raise ValueError("something broke")

    schema = ctx.openapi.load_schema({"/api/crash": {"get": {"responses": {"200": {"description": "OK"}}}}})
    strategy = schema["/api/crash"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=1, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        with pytest.raises(ValueError, match="something broke"):
            case.call(app=app)

    test()


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

    schema = ctx.openapi.load_schema({"/api/users": {"get": {"responses": {"404": {"description": "Missing"}}}}})
    response = schema["/api/users"]["GET"].Case().call(app=app)

    assert (response.status_code, response.json()) == (404, {"message": "No such user"})
