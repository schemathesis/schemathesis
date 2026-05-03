from __future__ import annotations

import csv
import json
from time import sleep
from typing import Any

import jsonschema_rs
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import BadRequest, GatewayTimeout, InternalServerError

from test.apps.fragments.schemas import PAYLOAD_SCHEMA

# base64("test:test")
_BASIC_AUTH_TOKEN = "Basic dGVzdDp0ZXN0"

_PAYLOAD_VALIDATOR = jsonschema_rs.Draft4Validator({"anyOf": [{"type": "null"}, PAYLOAD_SCHEMA]})


def register_success(app: Flask) -> None:
    @app.route("/api/success", methods=["GET"])
    def success_endpoint() -> Any:
        return jsonify({"success": True})


def register_failure(app: Flask) -> None:
    @app.route("/api/failure", methods=["GET"])
    def failure_endpoint() -> Any:
        raise InternalServerError


def register_multiple_failures(app: Flask) -> None:
    @app.route("/api/multiple_failures", methods=["GET"])
    def multiple_failures_endpoint() -> Any:
        try:
            id_value = int(request.args["id"])
        except KeyError:
            return jsonify({"detail": "Missing `id`"}), 400
        except ValueError:
            return jsonify({"detail": "Invalid `id`"}), 400
        if id_value == 0:
            raise InternalServerError
        if id_value > 0:
            raise GatewayTimeout
        return jsonify({"result": "OK"})


def register_payload(app: Flask) -> None:
    @app.route("/api/payload", methods=["POST"])
    def payload_endpoint() -> Any:
        try:
            data = request.json
            try:
                _PAYLOAD_VALIDATOR.validate(data)
            except jsonschema_rs.ValidationError:
                return jsonify({"detail": "Validation error"}), 400
        except BadRequest:
            data = {"name": "Nothing!"}
        return jsonify(data)


def register_unsatisfiable(app: Flask) -> None:
    @app.route("/api/unsatisfiable", methods=["POST"])
    def unsatisfiable_endpoint() -> Any:
        return jsonify({"result": "IMPOSSIBLE!"})


def register_multipart(app: Flask) -> None:
    @app.route("/api/multipart", methods=["POST"])
    def multipart_endpoint() -> Any:
        files = {name: value.stream.read().decode() for name, value in request.files.items()}
        return jsonify(**files, **request.form.to_dict())


def register_csv_payload(app: Flask) -> None:
    @app.route("/api/csv", methods=["POST"])
    def csv_endpoint() -> Any:
        if request.content_type and not request.content_type.startswith("text/csv"):
            return jsonify({"detail": f"Expected text/csv payload, got {request.content_type}"}), 500
        text = request.get_data(as_text=True)
        rows = list(csv.DictReader(text.splitlines())) if text else []
        return jsonify(rows)


def register_flaky(app: Flask) -> None:
    app.config["flaky_should_fail"] = True

    @app.route("/api/flaky", methods=["GET"])
    def flaky_endpoint() -> Any:
        if app.config["flaky_should_fail"]:
            app.config["flaky_should_fail"] = False
            raise InternalServerError
        return jsonify({"result": "flaky!"})


def register_ignored_auth(app: Flask) -> None:
    @app.route("/api/ignored_auth", methods=["GET"])
    def ignored_auth_endpoint() -> Any:
        return jsonify({"has_auth": "Authorization" in request.headers})


def register_slow(app: Flask) -> None:
    @app.route("/api/slow", methods=["GET"])
    def slow_endpoint() -> Any:
        sleep(0.5)
        return jsonify({"success": True})


def register_headers(app: Flask) -> None:
    @app.route("/api/headers", methods=["GET"])
    def headers_endpoint() -> Any:
        values = dict(request.headers)
        # Werkzeug rejects response headers containing CR/LF/NUL; drop those values
        # so fuzzed inputs don't kill the connection mid-response.
        safe = {k: v for k, v in values.items() if not any(c in v for c in "\r\n\x00")}
        return Response(json.dumps(values), content_type="application/json", headers=safe)


def register_path_variable(app: Flask) -> None:
    @app.route("/api/path_variable/<key>", methods=["GET"])
    def path_variable_endpoint(key: str) -> Any:
        return jsonify({"success": True})


def register_custom_format(app: Flask) -> None:
    @app.route("/api/custom_format", methods=["GET"])
    def custom_format_endpoint() -> Any:
        if "id" not in request.args:
            return jsonify({"detail": "Missing `id`"}), 400
        if not request.args["id"].isdigit():
            return jsonify({"detail": "Invalid `id`"}), 400
        return jsonify({"value": request.args["id"]})


def register_basic(app: Flask) -> None:
    @app.route("/api/basic", methods=["GET"])
    def basic_endpoint() -> Any:
        if request.headers.get("Authorization") == _BASIC_AUTH_TOKEN:
            return jsonify({"secret": 42})
        return {"detail": "Unauthorized"}, 401
