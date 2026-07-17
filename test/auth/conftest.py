import pytest
from flask import Response, jsonify

import schemathesis
import schemathesis.openapi


@pytest.fixture(autouse=True)
def unregister_global():
    yield
    schemathesis.auths.unregister()


@pytest.fixture
def auth_operation(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "get": {
                    "operationId": "getData",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/api/body-auth", methods=["POST"])
    def body_auth():
        return jsonify({"access_token": "test-token"})

    @app.route("/api/nested-auth", methods=["POST"])
    def nested_auth():
        return jsonify({"data": {"token": "test-token"}})

    @app.route("/api/header-auth", methods=["POST"])
    def header_auth():
        return jsonify({}), 200, {"X-Auth-Token": "test-token"}

    @app.route("/api/fail", methods=["POST"])
    def fail():
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/api/missing-key", methods=["POST"])
    def missing_key():
        return jsonify({"other": "value"})

    @app.route("/api/non-json", methods=["POST"])
    def non_json():
        return Response("not json", content_type="text/plain")

    @app.route("/api/wrong-type", methods=["POST"])
    def wrong_type():
        return jsonify({"token": 42})

    @app.route("/api/bad-charset-auth/<charset>", methods=["POST"])
    def bad_charset_auth(charset):
        return Response('{"access_token": "test-token"}', content_type=f"application/json; charset={charset}")

    @app.route("/api/bom-auth", methods=["POST"])
    def bom_auth():
        return Response(b'\xef\xbb\xbf{"access_token": "test-token"}', content_type="application/json")

    @app.route("/api/latin1-auth", methods=["POST"])
    def latin1_auth():
        return Response(
            '{"access_token": "café-token"}'.encode("latin-1"), content_type="application/json; charset=latin-1"
        )

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    return schema["/data"]["GET"]
