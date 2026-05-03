from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import InternalServerError

# base64("test:test")
_BASIC_AUTH_TOKEN = "Basic dGVzdDp0ZXN0"


def register_success(app: Flask) -> None:
    @app.route("/api/success", methods=["GET"])
    def success_endpoint() -> Any:
        return jsonify({"success": True})


def register_failure(app: Flask) -> None:
    @app.route("/api/failure", methods=["GET"])
    def failure_endpoint() -> Any:
        raise InternalServerError


def register_basic(app: Flask) -> None:
    @app.route("/api/basic", methods=["GET"])
    def basic_endpoint() -> Any:
        if request.headers.get("Authorization") == _BASIC_AUTH_TOKEN:
            return jsonify({"secret": 42})
        return {"detail": "Unauthorized"}, 401
