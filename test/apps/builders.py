from __future__ import annotations

from typing import Any

from flask import Flask, jsonify


def build_schema(paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: Any) -> dict[str, Any]:
    template: dict[str, Any] = {
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": paths,
    }
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

    @app.route("/openapi.json")
    def openapi_spec() -> Any:
        return jsonify(schema)

    return app


def make_flask_app(paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: Any) -> tuple[Flask, dict[str, Any]]:
    schema = build_schema(paths, version=version, **kwargs)
    return make_flask_app_from_schema(schema), schema
