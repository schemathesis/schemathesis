from __future__ import annotations

from typing import Any


def success() -> dict[str, Any]:
    return {"/api/success": {"get": {"responses": {"200": {"description": "Success"}}}}}


def failure() -> dict[str, Any]:
    return {
        "/api/failure": {
            "get": {
                "responses": {
                    "200": {"description": "Success"},
                    "default": {"description": "Default response"},
                }
            }
        }
    }


def basic() -> dict[str, Any]:
    return {
        "/api/basic": {
            "get": {
                "security": [{"basicAuth": []}],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"secret": {"type": "integer"}}}
                            }
                        },
                    }
                },
            }
        }
    }
