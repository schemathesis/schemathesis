from __future__ import annotations

from typing import Any

from flask import Flask, jsonify


def register_success(app: Flask) -> None:
    @app.route("/api/success", methods=["GET"])
    def success_endpoint() -> Any:
        return jsonify({"success": True})
