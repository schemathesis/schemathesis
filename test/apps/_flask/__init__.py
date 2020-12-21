import csv
import json
from collections import defaultdict
from time import sleep
from typing import Tuple

import yaml
from flask import Flask, Response, _request_ctx_stack, jsonify, request
from werkzeug.exceptions import BadRequest, GatewayTimeout, InternalServerError

try:
    from ..utils import Endpoint, OpenAPIVersion, make_openapi_schema
except (ImportError, ValueError):
    from utils import Endpoint, OpenAPIVersion, make_openapi_schema


def expect_content_type(value: str):
    if request.headers["Content-Type"] != value:
        raise InternalServerError(f"Expected {value} payload")


def create_openapi_app(
    endpoints: Tuple[str, ...] = ("success", "failure"), version: OpenAPIVersion = OpenAPIVersion("2.0")
) -> Flask:
    app = Flask("test_app")
    app.config["should_fail"] = True
    app.config["schema_data"] = make_openapi_schema(endpoints, version)
    app.config["incoming_requests"] = []
    app.config["schema_requests"] = []
    app.config["internal_exception"] = False
    app.config["users"] = {}
    app.config["requests_history"] = defaultdict(list)

    @app.before_request
    def store_request():
        current_request = _request_ctx_stack.top.request
        if request.path == "/schema.yaml":
            app.config["schema_requests"].append(current_request)
        else:
            app.config["incoming_requests"].append(current_request)

    @app.route("/schema.yaml")
    def schema():
        schema_data = app.config["schema_data"]
        content = yaml.dump(schema_data)
        return Response(content, content_type="text/plain")

    @app.route("/api/success", methods=["GET"])
    def success():
        if app.config["internal_exception"]:
            1 / 0
        return jsonify({"success": True})

    @app.route("/api/recursive", methods=["GET"])
    def recursive():
        return jsonify({"children": [{"children": [{"children": []}]}]})

    @app.route("/api/payload", methods=["POST"])
    def payload():
        try:
            data = request.json
        except BadRequest:
            data = {"name": "Nothing!"}
        return jsonify(data)

    @app.route("/api/get_payload", methods=["GET"])
    def get_payload():
        return jsonify(request.json)

    @app.route("/api/headers", methods=["GET"])
    def headers():
        values = dict(request.headers)
        return Response(json.dumps(values), content_type="application/json", headers=values)

    @app.route("/api/failure", methods=["GET"])
    def failure():
        raise InternalServerError

    @app.route("/api/multiple_failures", methods=["GET"])
    def multiple_failures():
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

    @app.route("/api/slow", methods=["GET"])
    def slow():
        sleep(0.1)
        return jsonify({"success": True})

    @app.route("/api/path_variable/<key>", methods=["GET"])
    def path_variable(key):
        return jsonify({"success": True})

    @app.route("/api/unsatisfiable", methods=["POST"])
    def unsatisfiable():
        return jsonify({"result": "IMPOSSIBLE!"})

    @app.route("/api/invalid", methods=["POST"])
    def invalid():
        return jsonify({"success": True})

    @app.route("/api/performance", methods=["POST"])
    def performance():
        data = request.json
        number = str(data).count("0")
        if number > 0:
            sleep(0.01 * number)
        if number > 10:
            raise InternalServerError
        return jsonify({"success": True})

    @app.route("/api/flaky", methods=["GET"])
    def flaky():
        if app.config["should_fail"]:
            app.config["should_fail"] = False
            raise InternalServerError
        return jsonify({"result": "flaky!"})

    @app.route("/api/multipart", methods=["POST"])
    def multipart():
        files = {name: value.stream.read().decode() for name, value in request.files.items()}
        return jsonify(**files, **request.form.to_dict())

    @app.route("/api/upload_file", methods=["POST"])
    def upload_file():
        return jsonify({"size": request.content_length})

    @app.route("/api/form", methods=["POST"])
    def form():
        expect_content_type("application/x-www-form-urlencoded")
        return jsonify({"size": request.content_length})

    @app.route("/api/csv", methods=["POST"])
    def csv_payload():
        expect_content_type("text/csv")
        data = request.get_data(as_text=True)
        if data:
            reader = csv.DictReader(data.splitlines())
            data = list(reader)
        else:
            data = []
        return jsonify(data)

    @app.route("/api/teapot", methods=["POST"])
    def teapot():
        return jsonify({"success": True}), 418

    @app.route("/api/text", methods=["GET"])
    def text():
        return Response("Text response", content_type="text/plain")

    @app.route("/api/text", methods=["POST"])
    def plain_text_body():
        expect_content_type("text/plain")
        return Response(request.data, content_type="text/plain")

    @app.route("/api/malformed_json", methods=["GET"])
    def malformed_json():
        return Response("{malformed}", content_type="application/json")

    @app.route("/api/invalid_response", methods=["GET"])
    def invalid_response():
        return jsonify({"random": "key"})

    @app.route("/api/custom_format", methods=["GET"])
    def custom_format():
        if "id" not in request.args:
            return jsonify({"detail": "Missing `id`"}), 400
        if not request.args["id"].isdigit():
            return jsonify({"detail": "Invalid `id`"}), 400
        return jsonify({"value": request.args["id"]})

    @app.route("/api/invalid_path_parameter/<id>", methods=["GET"])
    def invalid_path_parameter(id):
        return jsonify({"success": True})

    @app.route("/api/users/", methods=["POST"])
    def create_user():
        data = request.json
        if "username" not in data:
            return jsonify({"detail": "Missing `username`"}), 400
        if not isinstance(data["username"], str):
            return jsonify({"detail": "Invalid `username`"}), 400
        user_id = len(app.config["users"]) + 1
        app.config["users"][user_id] = {**data, "id": user_id}
        app.config["requests_history"][user_id].append("POST")
        return jsonify({"id": user_id}), 201

    @app.route("/api/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        try:
            user = app.config["users"][user_id]
            app.config["requests_history"][user_id].append("GET")
            return jsonify(user)
        except KeyError:
            return jsonify({"message": "Not found"}), 404

    @app.route("/api/users/<int:user_id>", methods=["PATCH"])
    def update_user(user_id):
        try:
            user = app.config["users"][user_id]
            history = app.config["requests_history"][user_id]
            history.append("PATCH")
            if history == ["POST", "GET", "PATCH", "GET", "PATCH"]:
                raise InternalServerError("We got a problem!")
            data = request.json
            if "username" not in data:
                return jsonify({"detail": "Missing `username`"}), 400
            if not isinstance(data["username"], str):
                return jsonify({"detail": "Invalid `username`"}), 400
            user["username"] = data["username"]
            return jsonify(user)
        except KeyError:
            return jsonify({"message": "Not found"}), 404

    return app
