from __future__ import annotations

from typing import Any


def success() -> dict[str, Any]:
    return {"/api/success": {"get": {"responses": {"200": {"description": "Success"}}}}}


def per_path_servers() -> dict[str, Any]:
    return {
        "/api/admin": {
            "servers": [{"url": "/zone-a"}],
            "get": {"responses": {"200": {"description": "OK"}}},
        },
        "/api/public": {
            "servers": [{"url": "/zone-b"}],
            "get": {"responses": {"200": {"description": "OK"}}},
        },
    }


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


def form() -> dict[str, Any]:
    return {
        "/api/form": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "additionalProperties": False,
                                "type": "object",
                                "properties": {
                                    "first_name": {"type": "string"},
                                    "last_name": {"type": "string"},
                                },
                                "required": ["first_name", "last_name"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def upload_file() -> dict[str, Any]:
    return {
        "/api/upload_file": {
            "post": {
                "x-property": 42,
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "data": {"type": "string", "format": "binary"},
                                    "note": {"type": "string"},
                                },
                                "required": ["data", "note"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "default": {"description": "Everything else"}},
            }
        }
    }


def always_incorrect() -> dict[str, Any]:
    # Server always returns non-2xx; used by the missing-test-data warning checks.
    return {
        "/api/always_incorrect": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def empty() -> dict[str, Any]:
    return {
        "/api/empty": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def empty_string() -> dict[str, Any]:
    return {
        "/api/empty_string": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"type": "string"}}},
                    }
                }
            }
        }
    }


def recursive() -> dict[str, Any]:
    return {
        "/api/recursive": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"$ref": "#/x-definitions/Node"}}},
                    }
                }
            }
        }
    }


def invalid_response() -> dict[str, Any]:
    return {
        "/api/invalid_response": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def invalid_path_parameter() -> dict[str, Any]:
    return {
        "/api/invalid_path_parameter/{id}": {
            "get": {
                "parameters": [{"name": "id", "in": "path", "required": False, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def missing_path_parameter() -> dict[str, Any]:
    # Path declares `{id}` but no `parameters` section — surfaces as a schema error.
    return {
        "/api/missing_path_parameter/{id}": {
            "get": {"responses": {"200": {"description": "OK"}}},
        }
    }


def reserved() -> dict[str, Any]:
    return {
        "/api/foo:bar": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def conformance() -> dict[str, Any]:
    # Schema requires `value` to be the literal "foo"; handler returns a fresh UUID — fails
    # response_schema_conformance every time.
    return {
        "/api/conformance": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"value": {"enum": ["foo"]}},
                                    "required": ["value"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def cp866() -> dict[str, Any]:
    return {
        "/api/cp866": {
            "get": {
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "string"}}}}
                }
            }
        }
    }


_READ_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "read": {"type": "string", "readOnly": True},
        "write": {"type": "integer", "writeOnly": True},
    },
    "required": ["read", "write"],
    "additionalProperties": False,
}


def read_only() -> dict[str, Any]:
    return {
        "/api/read_only": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadWrite"}}},
                    }
                }
            }
        }
    }


def write_only() -> dict[str, Any]:
    return {
        "/api/write_only": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadWrite"}}},
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadWrite"}}},
                    }
                },
            }
        }
    }


READ_WRITE_COMPONENTS: dict[str, Any] = {"schemas": {"ReadWrite": _READ_WRITE_SCHEMA}}


def text() -> dict[str, Any]:
    # Schema declares JSON; handler returns text/plain — used to test content-type checks.
    return {
        "/api/text": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def plain_text_body() -> dict[str, Any]:
    return {
        "/api/text": {
            "post": {
                "requestBody": {"content": {"text/plain": {"schema": {"type": "string"}}}, "required": True},
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def teapot() -> dict[str, Any]:
    # Handler returns 418 (an undocumented status); used to verify status_code_conformance.
    return {
        "/api/teapot": {
            "post": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def malformed_json() -> dict[str, Any]:
    return {
        "/api/malformed_json": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def invalid() -> dict[str, Any]:
    # The `type: int` is an intentional typo — should be "integer". Used to test schema validation errors.
    return {
        "/api/invalid": {
            "post": {
                "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "int"}}],
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


_SESSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["sessionId"],
    "properties": {"sessionId": {"type": "string", "format": "uuid"}},
}

_LOG_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["sessionId", "message"],
    "properties": {
        "sessionId": {"type": "string", "format": "uuid"},
        "message": {"type": "string"},
    },
}


def sessions_and_log_event() -> dict[str, Any]:
    return {
        "/api/sessions": {
            "post": {
                "operationId": "createSession",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _SESSION_SCHEMA,
                            "examples": {"valid": {"value": {"sessionId": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"}}},
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {"application/json": {"schema": _SESSION_SCHEMA}},
                    }
                },
            }
        },
        "/api/log_event": {
            "post": {
                "operationId": "logEvent",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": _LOG_EVENT_SCHEMA}},
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        },
    }


def deep_leaf_bug() -> dict[str, Any]:
    return {
        "/api/deep_leaf_bug": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeepLeafRoot"}}},
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }


def deep_leaf_bug_components() -> dict[str, Any]:
    return {
        "DeepLeafRoot": {
            "type": "object",
            "required": ["root_str", "depth1"],
            "additionalProperties": False,
            "properties": {
                "root_str": {"type": "string", "minLength": 4, "maxLength": 12, "pattern": "^[a-z]+$"},
                "root_int": {"type": "integer", "minimum": 0, "maximum": 100},
                "depth1": {"$ref": "#/components/schemas/DeepLeafDepth1"},
            },
        },
        "DeepLeafDepth1": {
            "type": "object",
            "required": ["d1_enum", "depth2"],
            "additionalProperties": False,
            "properties": {
                "d1_enum": {"type": "string", "enum": ["alpha", "beta", "gamma"]},
                "d1_num": {"type": "number", "minimum": 1.5, "maximum": 9.5},
                "depth2": {"$ref": "#/components/schemas/DeepLeafDepth2"},
            },
        },
        "DeepLeafDepth2": {
            "type": "object",
            "required": ["d2_str", "depth3"],
            "additionalProperties": False,
            "properties": {
                "d2_str": {"type": "string", "minLength": 2, "maxLength": 6, "pattern": "^[A-Z]{2,6}$"},
                "d2_arr": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {"type": "integer", "minimum": 10, "maximum": 99},
                },
                "depth3": {"$ref": "#/components/schemas/DeepLeafDepth3"},
            },
        },
        "DeepLeafDepth3": {
            "type": "object",
            "required": ["leaf_int"],
            "additionalProperties": False,
            "properties": {
                "leaf_uuid": {"type": "string", "format": "uuid"},
                "leaf_email": {"type": "string", "format": "email"},
                "leaf_int": {"type": "integer", "minimum": -5, "maximum": 5},
            },
        },
    }


def header_constraint_bug() -> dict[str, Any]:
    return {
        "/api/header_constraint_bug": {
            "get": {
                "parameters": [
                    {
                        "name": "X-Token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "pattern": "^[A-Z]{8}$"},
                    }
                ],
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }


def query_array_items_bug() -> dict[str, Any]:
    return {
        "/api/query_array_items_bug": {
            "get": {
                "parameters": [
                    {
                        "name": "ids",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 99}},
                    }
                ],
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }


def one_of_branch_bug() -> dict[str, Any]:
    return {
        "/api/one_of_branch_bug": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "oneOf": [
                                    {
                                        "type": "object",
                                        "required": ["kind", "value"],
                                        "properties": {
                                            "kind": {"type": "string", "enum": ["A"]},
                                            "value": {"type": "integer", "minimum": 0},
                                        },
                                    },
                                    {
                                        "type": "object",
                                        "required": ["kind", "nested"],
                                        "properties": {
                                            "kind": {"type": "string", "enum": ["B"]},
                                            "nested": {
                                                "type": "object",
                                                "required": ["pin"],
                                                "properties": {
                                                    "pin": {"type": "string", "pattern": "^[0-9]{4}$"},
                                                },
                                            },
                                        },
                                    },
                                ],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }


def additional_properties_bug() -> dict[str, Any]:
    return {
        "/api/additional_properties_bug": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "additionalProperties": {"type": "string", "format": "uuid"},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }
