import csv
import json
import logging
from time import sleep
from uuid import uuid4

import jsonschema
import yaml
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import BadRequest, GatewayTimeout, InternalServerError

from schemathesis.core import media_types

from ..schema import PAYLOAD_VALIDATOR, OpenAPIVersion, make_openapi_schema

logging.getLogger("werkzeug").setLevel(logging.CRITICAL)

SUCCESS_RESPONSE = {"read": "success!"}


def expect_content_type(value: str):
    content_type = request.headers["Content-Type"]
    main, sub = media_types.parse(content_type)
    if f"{main}/{sub}" != value:
        raise InternalServerError(f"Expected {value} payload")


def create_app(
    operations: tuple[str, ...] = ("success", "failure"), version: OpenAPIVersion = OpenAPIVersion("2.0")
) -> Flask:
    app = Flask("test_app")
    app.config["should_fail"] = True
    app.config["schema_data"] = make_openapi_schema(operations, version)
    app.config["incoming_requests"] = []
    app.config["schema_requests"] = []
    app.config["internal_exception"] = False
    app.config["random_delay"] = False
    app.config["users"] = {}

    @app.before_request
    def store_request():
        current_request = request._get_current_object()
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
            raise ZeroDivisionError("division by zero")
        return jsonify({"success": True})

    @app.route("/api/foo:bar", methods=["GET"])
    def reserved():
        return jsonify({"success": True})

    @app.route("/api/recursive", methods=["GET"])
    def recursive():
        return jsonify({"children": [{"children": [{"children": []}]}]})

    @app.route("/api/payload", methods=["POST"])
    def payload():
        try:
            data = request.json
            try:
                PAYLOAD_VALIDATOR.validate(data)
            except jsonschema.ValidationError:
                return jsonify({"detail": "Validation error"}), 400
        except BadRequest:
            data = {"name": "Nothing!"}
        return jsonify(data)

    @app.route("/api/get_payload", methods=["GET"])
    def get_payload():
        return jsonify(request.json)

    @app.route("/api/basic", methods=["GET"])
    def basic():
        if "Authorization" in request.headers and request.headers["Authorization"] == "Basic dGVzdDp0ZXN0":
            return jsonify({"secret": 42})
        return {"detail": "Unauthorized"}, 401

    @app.route("/api/empty", methods=["GET"])
    def empty():
        return Response(status=204)

    @app.route("/api/empty_string", methods=["GET"])
    def empty_string():
        return Response(response="")

    @app.route("/api/headers", methods=["GET"])
    def headers():
        values = dict(request.headers)
        return Response(json.dumps(values), content_type="application/json", headers=values)

    @app.route("/api/conformance", methods=["GET"])
    def conformance():
        # The schema expects `value` to be "foo", but it is different every time
        return jsonify({"value": uuid4().hex})

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
        if app.config["random_delay"]:
            sleep(app.config["random_delay"])
            app.config["random_delay"] = False
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
        data = request.form
        for field in ("first_name", "last_name"):
            if field not in data:
                return jsonify({"detail": f"Missing `{field}`"}), 400
            if not isinstance(data[field], str):
                return jsonify({"detail": f"Invalid `{field}`"}), 400
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

    @app.route("/api/read_only", methods=["GET"])
    def read_only():
        return jsonify(SUCCESS_RESPONSE)

    @app.route("/api/write_only", methods=["POST"])
    def write_only():
        data = request.get_json()
        if len(data) == 1 and isinstance(data["write"], int):
            return jsonify(SUCCESS_RESPONSE)
        raise InternalServerError

    @app.route("/api/text", methods=["GET"])
    def text():
        return Response("Text response", content_type="text/plain")

    @app.route("/api/cp866", methods=["GET"])
    def cp866():
        # NOTE. Setting `Response.charset` don't have effect in test client as it re-wraps this response with the
        # default one where `charset` is `utf-8`
        return Response("Тест".encode("cp866"), content_type="text/plain;charset=cp866")

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

    @app.route("/api/ignored_auth", methods=["GET"])
    def ignored_auth():
        return jsonify({"has_auth": "Authorization" in request.headers})

    @app.route("/api/invalid_path_parameter/<id>", methods=["GET"])
    def invalid_path_parameter(id):
        return jsonify({"success": True})

    @app.route("/api/users/", methods=["POST"])
    def create_user():
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"detail": "Invalid payload"}), 400
        for field in ("first_name", "last_name"):
            if field not in data:
                return jsonify({"detail": f"Missing `{field}`"}), 400
            if not isinstance(data[field], str):
                return jsonify({"detail": f"Invalid `{field}`"}), 400
        user_id = str(uuid4())
        app.config["users"][user_id] = {**data, "id": user_id}
        return jsonify({"id": user_id}), 201

    @app.route("/api/users/<user_id>", methods=["GET"])
    def get_user(user_id):
        try:
            user = app.config["users"][user_id]
            # The full name is done specifically via concatenation to trigger a bug when the last name is `None`
            full_name = user["first_name"] + " " + user["last_name"]
            return jsonify({"id": user["id"], "full_name": full_name})
        except KeyError:
            return jsonify({"message": "Not found"}), 404

    @app.route("/api/users/<user_id>", methods=["PATCH"])
    def update_user(user_id):
        try:
            user = app.config["users"][user_id]
            data = request.json
            for field in ("first_name", "last_name"):
                if field not in data:
                    return jsonify({"detail": f"Missing `{field}`"}), 400
                # Here we don't check the input value type to emulate a bug in another operation
                user[field] = data[field]
            return jsonify(user)
        except KeyError:
            return jsonify({"message": "Not found"}), 404

    return app
