from __future__ import annotations

import csv
import json
import uuid
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


_SUCCESS_RESPONSE = {"read": "success!"}


def register_form(app: Flask) -> None:
    @app.route("/api/form", methods=["POST"])
    def form_endpoint() -> Any:
        if not (request.content_type or "").startswith("application/x-www-form-urlencoded"):
            return jsonify({"detail": "Expected application/x-www-form-urlencoded payload"}), 500
        data = request.form
        for field in ("first_name", "last_name"):
            if field not in data:
                return jsonify({"detail": f"Missing `{field}`"}), 400
            if not isinstance(data[field], str):
                return jsonify({"detail": f"Invalid `{field}`"}), 400
        return jsonify({"size": request.content_length})


def register_upload_file(app: Flask) -> None:
    @app.route("/api/upload_file", methods=["POST"])
    def upload_file_endpoint() -> Any:
        return jsonify({"size": request.content_length})


def register_always_incorrect(app: Flask) -> None:
    @app.route("/api/always_incorrect", methods=["GET"])
    def always_incorrect_endpoint() -> Any:
        return Response('{"detail": "Always incorrect"}', status=400, content_type="application/json")


def register_empty(app: Flask) -> None:
    @app.route("/api/empty", methods=["GET"])
    def empty_endpoint() -> Any:
        return Response(status=204)


def register_empty_string(app: Flask) -> None:
    @app.route("/api/empty_string", methods=["GET"])
    def empty_string_endpoint() -> Any:
        return Response(response="")


def register_recursive(app: Flask) -> None:
    @app.route("/api/recursive", methods=["GET"])
    def recursive_endpoint() -> Any:
        return jsonify({"children": [{"children": [{"children": []}]}]})


def register_invalid_response(app: Flask) -> None:
    @app.route("/api/invalid_response", methods=["GET"])
    def invalid_response_endpoint() -> Any:
        return jsonify({"random": "key"})


def register_invalid_path_parameter(app: Flask) -> None:
    @app.route("/api/invalid_path_parameter/<path_id>", methods=["GET"])
    def invalid_path_parameter_endpoint(path_id: str) -> Any:
        return jsonify({"success": True})


def register_reserved(app: Flask) -> None:
    @app.route("/api/foo:bar", methods=["GET"])
    def reserved_endpoint() -> Any:
        return jsonify({"success": True})


def register_conformance(app: Flask) -> None:
    @app.route("/api/conformance", methods=["GET"])
    def conformance_endpoint() -> Any:
        # Returns a fresh UUID where the schema requires the literal "foo".
        return jsonify({"value": uuid.uuid4().hex})


def register_cp866(app: Flask) -> None:
    @app.route("/api/cp866", methods=["GET"])
    def cp866_endpoint() -> Any:
        return Response("Тест".encode("cp866"), content_type="text/plain;charset=cp866")


def register_read_only(app: Flask) -> None:
    @app.route("/api/read_only", methods=["GET"])
    def read_only_endpoint() -> Any:
        return jsonify(_SUCCESS_RESPONSE)


def register_write_only(app: Flask) -> None:
    @app.route("/api/write_only", methods=["POST"])
    def write_only_endpoint() -> Any:
        data = request.get_json()
        if isinstance(data, dict) and len(data) == 1 and isinstance(data.get("write"), int):
            return jsonify(_SUCCESS_RESPONSE)
        raise InternalServerError


def register_text(app: Flask) -> None:
    @app.route("/api/text", methods=["GET"])
    def text_endpoint() -> Any:
        return Response("Text response", content_type="text/plain")


def register_plain_text_body(app: Flask) -> None:
    @app.route("/api/text", methods=["POST"])
    def plain_text_body_endpoint() -> Any:
        if not (request.content_type or "").startswith("text/plain"):
            return Response("Expected text/plain payload", status=500, content_type="text/plain")
        return Response(request.get_data(), content_type="text/plain")


def register_teapot(app: Flask) -> None:
    @app.route("/api/teapot", methods=["POST"])
    def teapot_endpoint() -> Any:
        return jsonify({"success": True}), 418


def register_malformed_json(app: Flask) -> None:
    @app.route("/api/malformed_json", methods=["GET"])
    def malformed_json_endpoint() -> Any:
        return Response("{malformed}", content_type="application/json")


def register_invalid(app: Flask) -> None:
    @app.route("/api/invalid", methods=["POST"])
    def invalid_endpoint() -> Any:
        return jsonify({"success": True})


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
