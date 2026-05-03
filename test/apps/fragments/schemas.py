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


def multiple_failures() -> dict[str, Any]:
    return {
        "/api/multiple_failures": {
            "get": {
                "parameters": [
                    {"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer", "minimum": 0, "exclusiveMinimum": True},
        "boolean": {"type": "boolean"},
        "nested": {
            "type": "array",
            "items": {
                "type": "integer",
                "minimum": 0,
                "exclusiveMinimum": True,
                "maximum": 10,
                "exclusiveMaximum": True,
            },
        },
    },
    "required": ["name"],
    "example": {"name": "John"},
    "additionalProperties": False,
}


def payload() -> dict[str, Any]:
    return {
        "/api/payload": {
            "post": {
                "requestBody": {"content": {"application/json": {"schema": PAYLOAD_SCHEMA}}},
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": PAYLOAD_SCHEMA}}}
                },
            }
        }
    }


def unsatisfiable() -> dict[str, Any]:
    return {
        "/api/unsatisfiable": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"allOf": [{"type": "integer"}, {"type": "string"}]}}},
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def flaky() -> dict[str, Any]:
    return {
        "/api/flaky": {
            "get": {
                "parameters": [
                    {"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def ignored_auth() -> dict[str, Any]:
    return {
        "/api/ignored_auth": {
            "get": {
                "security": [{"heisenAuth": []}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def multipart() -> dict[str, Any]:
    return {
        "/api/multipart": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "key": {"type": "string"},
                                    "value": {"type": "integer"},
                                    "maybe": {"type": "boolean"},
                                },
                                "required": ["key", "value"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def csv_payload() -> dict[str, Any]:
    return {
        "/api/csv": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "text/csv": {
                            "schema": {
                                "type": "array",
                                "items": {
                                    "additionalProperties": False,
                                    "type": "object",
                                    "properties": {
                                        "first_name": {"type": "string", "pattern": r"\A[A-Za-z]*\Z"},
                                        "last_name": {"type": "string", "pattern": r"\A[A-Za-z]*\Z"},
                                    },
                                    "required": ["first_name", "last_name"],
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def slow() -> dict[str, Any]:
    return {
        "/api/slow": {
            "get": {"responses": {"200": {"description": "OK"}}},
        }
    }


def headers() -> dict[str, Any]:
    return {
        "/api/headers": {
            "get": {
                "security": [{"api_key": []}],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                        "headers": {
                            "X-Custom-Header": {
                                "description": "Custom header",
                                "schema": {"type": "integer"},
                                "required": True,
                            }
                        },
                    },
                    "default": {"description": "Default response"},
                },
            }
        }
    }


def path_variable() -> dict[str, Any]:
    return {
        "/api/path_variable/{key}": {
            "get": {
                "parameters": [
                    {
                        "name": "key",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1},
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def custom_format() -> dict[str, Any]:
    return {
        "/api/custom_format": {
            "get": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string", "format": "digits"},
                    },
                ],
                "responses": {
                    "200": {"description": "OK"},
                    "default": {"description": "Everything else"},
                },
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
