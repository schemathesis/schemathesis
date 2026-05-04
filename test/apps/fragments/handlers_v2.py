"""Flask handlers for Swagger 2.0 fragments. Routes mounted under /api (Swagger basePath)."""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, jsonify, request


def register_baseline(app: Flask) -> None:
    @app.route("/api/baseline", methods=["GET"])
    def baseline_endpoint() -> Any:
        return jsonify({"ok": True})


def register_formdata(app: Flask) -> None:
    @app.route("/api/upload", methods=["POST"])
    def upload_endpoint() -> Any:
        # Capture form fields and files so tests can introspect what was sent.
        title = request.form.get("title")
        files = list(request.files.keys())
        return jsonify({"title": title, "files": files})


def register_collection_format(app: Flask) -> None:
    @app.route("/api/search", methods=["GET"])
    def search_endpoint() -> Any:
        # Echo each delimiter variant raw so tests can assert the exact wire form.
        return jsonify(
            {
                "csv": request.query_string.decode().split("&"),
                "raw": {key: request.args.getlist(key) for key in ("csv", "ssv", "tsv", "pipes")},
            }
        )


def register_security(app: Flask) -> None:
    @app.route("/api/private/api-key", methods=["GET"])
    def api_key_endpoint() -> Any:
        return jsonify({"key": request.headers.get("X-API-Key", "")})

    @app.route("/api/private/basic", methods=["GET"])
    def basic_endpoint() -> Any:
        return jsonify({"auth": request.headers.get("Authorization", "")})

    @app.route("/api/private/optional", methods=["GET"])
    def optional_endpoint() -> Any:
        return jsonify({"auth": request.headers.get("Authorization", "")})


def register_nullable(app: Flask) -> None:
    @app.route("/api/nullable/<id>", methods=["GET"])
    def nullable_endpoint(id: str) -> Any:
        # `tag` is null — schema marks it x-nullable so this must validate.
        return jsonify({"name": id, "tag": None})


def register_examples(app: Flask) -> None:
    @app.route("/api/examples", methods=["POST"])
    def examples_endpoint() -> Any:
        return jsonify({"echo": "hi"})


def register_response_headers(app: Flask) -> None:
    @app.route("/api/headers", methods=["GET"])
    def headers_endpoint() -> Any:
        response = jsonify({})
        response.headers["X-Total-Count"] = "42"
        response.headers["X-Tags"] = "alpha,beta,gamma"
        return response


def register_default_response(app: Flask) -> None:
    @app.route("/api/errors", methods=["GET"])
    def errors_endpoint() -> Any:
        # Status 418 has no documented entry; the engine falls back to `default`.
        return Response(
            response='{"code": 418, "message": "I am a teapot"}',
            status=418,
            content_type="application/json",
        )


def register_array_path_parameter(app: Flask) -> None:
    @app.route("/api/items/<ids>", methods=["GET"])
    def items_endpoint(ids: str) -> Any:
        return jsonify({"ids": ids.split(",")})


def register_injected_path_parameter(app: Flask) -> None:
    @app.route("/api/auto/<name>", methods=["GET"])
    def auto_endpoint(name: str) -> Any:
        return jsonify({"name": name})


def register_all_locations(app: Flask) -> None:
    @app.route("/api/all/<path_param>", methods=["POST"])
    def all_locations_endpoint(path_param: str) -> Any:
        return jsonify(
            {
                "path": path_param,
                "query": request.args.get("query_param"),
                "header": request.headers.get("X-Header-Param"),
                "body": request.get_json(silent=True),
            }
        )


def register_oauth2_security(app: Flask) -> None:
    @app.route("/api/oauth-protected", methods=["GET"])
    def oauth_endpoint() -> Any:
        return jsonify({})


def register_no_response_body(app: Flask) -> None:
    @app.route("/api/no-content", methods=["DELETE"])
    def no_content_endpoint() -> Any:
        return Response(status=204)


def register_native_response_examples(app: Flask) -> None:
    @app.route("/api/items", methods=["GET"])
    def items_listing_endpoint() -> Any:
        return jsonify([{"id": 1}, {"id": 2}])


def register_parameter_ref(app: Flask) -> None:
    @app.route("/api/listing", methods=["GET"])
    def listing_endpoint() -> Any:
        return jsonify({"page": request.args.get("page")})


def register_path_level_parameters(app: Flask) -> None:
    @app.route("/api/path-shared/<token>", methods=["GET", "POST"])
    def path_shared_endpoint(token: str) -> Any:
        return jsonify({"token": token, "trace": request.args.get("trace")})


def register_form_urlencoded(app: Flask) -> None:
    @app.route("/api/form-urlencoded", methods=["POST"])
    def form_urlencoded_endpoint() -> Any:
        return jsonify({"field_a": request.form.get("field_a"), "field_b": request.form.get("field_b")})


def register_multi_path_parameter(app: Flask) -> None:
    @app.route("/api/orgs/<org_id>/users/<user_id>", methods=["GET"])
    def multi_path_endpoint(org_id: str, user_id: str) -> Any:
        return jsonify({"org_id": org_id, "user_id": user_id})


def register_diverse_response_headers(app: Flask) -> None:
    @app.route("/api/diverse-headers", methods=["GET"])
    def diverse_headers_endpoint() -> Any:
        response = jsonify({})
        response.headers["X-Rate-Limit-Remaining"] = "42"
        response.headers["X-Deprecated"] = "false"
        response.headers["X-Timestamp"] = "2026-01-01T00:00:00Z"
        return response


def register_array_response_header(app: Flask) -> None:
    @app.route("/api/array-header", methods=["GET"])
    def array_header_endpoint() -> Any:
        response = jsonify({})
        response.headers["X-Tags"] = "alpha,beta"
        return response


def register_and_security(app: Flask) -> None:
    @app.route("/api/private/and", methods=["GET"])
    def and_security_endpoint() -> Any:
        return jsonify(
            {
                "api_key": request.headers.get("X-API-Key", ""),
                "auth": request.headers.get("Authorization", ""),
            }
        )
