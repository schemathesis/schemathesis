"""Swagger 2.0 schema fragments — paths only.

`build_schema(paths, version="2.0")` adds `swagger`, `info`, and `basePath: /api`.
Components like `securityDefinitions` are passed as kwargs to `build_schema`.
"""

from __future__ import annotations

from typing import Any


def baseline() -> dict[str, Any]:
    return {
        "/baseline": {
            "get": {
                "produces": ["application/json"],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                        },
                    }
                },
            }
        }
    }


def formdata() -> dict[str, Any]:
    return {
        "/upload": {
            "post": {
                "consumes": ["multipart/form-data"],
                "produces": ["application/json"],
                "parameters": [
                    {"name": "title", "in": "formData", "type": "string", "required": True, "minLength": 1},
                    {"name": "file", "in": "formData", "type": "file", "required": True},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def collection_format() -> dict[str, Any]:
    array_param = lambda name, fmt: {  # noqa: E731
        "name": name,
        "in": "query",
        "type": "array",
        "items": {"type": "string"},
        "collectionFormat": fmt,
        "required": True,
    }
    return {
        "/search": {
            "get": {
                "produces": ["application/json"],
                "parameters": [
                    array_param("csv", "csv"),
                    array_param("ssv", "ssv"),
                    array_param("tsv", "tsv"),
                    array_param("pipes", "pipes"),
                    array_param("multi", "multi"),
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"type": "array", "items": {"type": "string"}},
                    }
                },
            }
        }
    }


def security() -> dict[str, Any]:
    return {
        "/private/api-key": {
            "get": {
                "security": [{"api_key": []}],
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/private/basic": {
            "get": {
                "security": [{"basic_auth": []}],
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/private/optional": {
            "get": {
                # Empty `security` list — auth is optional for this operation.
                "security": [],
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }


SECURITY_DEFINITIONS: dict[str, Any] = {
    "api_key": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
    "basic_auth": {"type": "basic"},
}


def nullable() -> dict[str, Any]:
    return {
        "/nullable/{id}": {
            "get": {
                "produces": ["application/json"],
                "parameters": [{"name": "id", "in": "path", "type": "string", "required": True}],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "tag": {"type": "string", "x-nullable": True},
                            },
                            "required": ["name", "tag"],
                        },
                    }
                },
            }
        }
    }


def examples() -> dict[str, Any]:
    return {
        "/examples": {
            "post": {
                "consumes": ["application/json"],
                "produces": ["application/json"],
                "parameters": [
                    {
                        "name": "body",
                        "in": "body",
                        "required": True,
                        "schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                            "x-example": {"name": "from-x-example"},
                        },
                    }
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"type": "object"},
                        "x-examples": {
                            "default": {"summary": "Default", "value": {"echo": "hi"}},
                        },
                    }
                },
            }
        }
    }


def response_headers() -> dict[str, Any]:
    return {
        "/headers": {
            "get": {
                "produces": ["application/json"],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"type": "object"},
                        "headers": {
                            "X-Total-Count": {"type": "integer"},
                            "X-Tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "collectionFormat": "csv",
                            },
                        },
                    }
                },
            }
        }
    }


def default_response() -> dict[str, Any]:
    return {
        "/errors": {
            "get": {
                "produces": ["application/json"],
                "responses": {
                    "default": {
                        "description": "Error",
                        "schema": {
                            "type": "object",
                            "properties": {"code": {"type": "integer"}, "message": {"type": "string"}},
                            "required": ["code", "message"],
                        },
                    }
                },
            }
        }
    }


def array_path_parameter() -> dict[str, Any]:
    # Path with an array param + collectionFormat: csv. Exercises build_path_parameter_v2.
    return {
        "/items/{ids}": {
            "parameters": [
                {
                    "name": "ids",
                    "in": "path",
                    "type": "array",
                    "items": {"type": "string"},
                    "collectionFormat": "csv",
                    "required": True,
                }
            ],
            "get": {
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            },
        }
    }


def injected_path_parameter() -> dict[str, Any]:
    # `{name}` appears in the path template but is not declared in `parameters` —
    # the loader must inject a default path parameter at registration time.
    return {
        "/auto/{name}": {
            "get": {
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def all_locations() -> dict[str, Any]:
    # path + query + header + body parameter on one operation, plus a $ref'd body
    # schema, to walk the broader iter_parameters_v2 / extract_parameter_schema_v2 branches.
    return {
        "/all/{path_param}": {
            "post": {
                "consumes": ["application/json"],
                "produces": ["application/json"],
                "parameters": [
                    {"name": "path_param", "in": "path", "type": "string", "required": True},
                    {"name": "query_param", "in": "query", "type": "integer", "required": True},
                    {"name": "X-Header-Param", "in": "header", "type": "string", "required": True, "minLength": 1},
                    {
                        "name": "body",
                        "in": "body",
                        "required": True,
                        "schema": {"$ref": "#/definitions/Payload"},
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


PAYLOAD_DEFINITION: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
}


def oauth2_security() -> dict[str, Any]:
    return {
        "/oauth-protected": {
            "get": {
                "security": [{"oauth2_implicit": ["read"]}],
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


OAUTH2_SECURITY_DEFINITIONS: dict[str, Any] = {
    "oauth2_implicit": {
        "type": "oauth2",
        "flow": "implicit",
        "authorizationUrl": "https://example.com/oauth/authorize",
        "scopes": {"read": "Read access"},
    },
}


def no_response_body() -> dict[str, Any]:
    # 204-style response with no `schema` key — exercises the early `None` return
    # in extract_response_schema_v2 instead of the body-validation path.
    return {
        "/no-content": {
            "delete": {
                "produces": ["application/json"],
                "responses": {"204": {"description": "No Content"}},
            }
        }
    }


def native_response_examples() -> dict[str, Any]:
    # Swagger 2.0 native: `examples` under a response, keyed by media type.
    return {
        "/items": {
            "get": {
                "produces": ["application/json"],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                        },
                        "examples": {
                            "application/json": [{"id": 1}, {"id": 2}],
                        },
                    }
                },
            }
        }
    }


def parameter_ref() -> dict[str, Any]:
    # Operation references a shared parameter via `$ref: #/parameters/Pagination`.
    return {
        "/listing": {
            "get": {
                "produces": ["application/json"],
                "parameters": [{"$ref": "#/parameters/Pagination"}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


SHARED_PARAMETERS: dict[str, Any] = {
    "Pagination": {
        "name": "page",
        "in": "query",
        "type": "integer",
        "minimum": 1,
        "required": True,
    },
}


def path_level_parameters() -> dict[str, Any]:
    # `parameters` declared at the path-item level applies to every operation underneath.
    return {
        "/path-shared/{token}": {
            "parameters": [
                {"name": "token", "in": "path", "type": "string", "required": True, "minLength": 1},
                {"name": "trace", "in": "query", "type": "string", "required": True, "minLength": 1},
            ],
            "get": {
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            },
        }
    }


def form_urlencoded() -> dict[str, Any]:
    return {
        "/form-urlencoded": {
            "post": {
                "consumes": ["application/x-www-form-urlencoded"],
                "produces": ["application/json"],
                "parameters": [
                    {"name": "field_a", "in": "formData", "type": "string", "required": True, "minLength": 1},
                    {"name": "field_b", "in": "formData", "type": "integer", "required": True},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def multi_path_parameter() -> dict[str, Any]:
    return {
        "/orgs/{org_id}/users/{user_id}": {
            "get": {
                "produces": ["application/json"],
                "parameters": [
                    {"name": "org_id", "in": "path", "type": "string", "required": True, "minLength": 1},
                    {"name": "user_id", "in": "path", "type": "string", "required": True, "minLength": 1},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def diverse_response_headers() -> dict[str, Any]:
    return {
        "/diverse-headers": {
            "get": {
                "produces": ["application/json"],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"type": "object"},
                        "headers": {
                            "X-Rate-Limit-Remaining": {"type": "integer", "minimum": 0},
                            "X-Deprecated": {"type": "boolean"},
                            "X-Timestamp": {"type": "string", "format": "date-time"},
                        },
                    }
                },
            }
        }
    }


def array_response_header() -> dict[str, Any]:
    # Isolated CSV-array response header. Currently fails validation because the
    # check does not decode `collectionFormat` before evaluating against `type: array`.
    return {
        "/array-header": {
            "get": {
                "produces": ["application/json"],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"type": "object"},
                        "headers": {
                            "X-Tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "collectionFormat": "csv",
                            }
                        },
                    }
                },
            }
        }
    }


def and_security() -> dict[str, Any]:
    # Single requirement object carrying both schemes — the engine must add both headers.
    return {
        "/private/and": {
            "get": {
                "security": [{"api_key": [], "basic_auth": []}],
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
