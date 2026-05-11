from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, request

from test.apps.runtime import CapturedRequest


def build_schema(paths: dict[str, Any] | None, *, version: str = "3.0.2", **kwargs: Any) -> dict[str, Any]:
    template: dict[str, Any] = {
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
    }
    if paths is not None:
        template["paths"] = paths
    if version.startswith("3"):
        template["openapi"] = version
    elif version.startswith("2"):
        template["swagger"] = version
        template["basePath"] = "/api"
    else:
        raise ValueError("Unknown version")
    return {**template, **kwargs}


def make_flask_app_from_schema(schema: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    captured: list[CapturedRequest] = []
    schema_fetches: list[CapturedRequest] = []
    app.config["captured_requests"] = captured
    app.config["captured_schema_requests"] = schema_fetches

    @app.before_request
    def _capture_request() -> None:
        # Skip startup capability probes — they are infrastructure, not user-facing requests.
        if "X-Schemathesis-Probe" in request.headers:
            return
        snapshot = CapturedRequest(
            method=request.method,
            path=request.path,
            query=dict(request.args),
            headers=dict(request.headers),
            body=request.get_data(),
            raw_query=request.query_string.decode("utf-8", errors="replace"),
        )
        if request.path == "/openapi.json":
            schema_fetches.append(snapshot)
        else:
            captured.append(snapshot)

    @app.route("/openapi.json")
    def openapi_spec() -> Any:
        return jsonify(schema)

    @app.errorhandler(404)
    def not_found(_error: Any) -> tuple[str, int]:
        return "404: Not Found", 404

    @app.errorhandler(405)
    def method_not_allowed(error: Any) -> Any:
        # Preserve the Allow header that Flask emits by default; only swap the body.
        response = error.get_response()
        response.set_data("405: Method Not Allowed")
        response.content_type = "text/plain"
        return response

    @app.errorhandler(500)
    def internal_server_error(_error: Any) -> tuple[str, int]:
        return "500: Internal Server Error", 500

    return app


def make_flask_app(paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: Any) -> tuple[Flask, dict[str, Any]]:
    schema = build_schema(paths, version=version, **kwargs)
    return make_flask_app_from_schema(schema), schema


_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def make_permissive_flask_app(schema: dict[str, Any]) -> Flask:
    # Answers 200 to any unmatched path so tests that only care about CLI behavior
    # don't accumulate 404 noise from generated requests.
    app = make_flask_app_from_schema(schema)

    @app.route("/<path:_unused>", methods=_HTTP_METHODS)
    def _ok(_unused: str) -> Any:
        return jsonify({}), 200

    return app
