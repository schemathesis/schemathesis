from enum import Enum
from typing import Any, Dict, Tuple

import jsonschema


class Endpoint(Enum):
    success = ("GET", "/api/success")
    failure = ("GET", "/api/failure")
    payload = ("POST", "/api/payload")
    # Not compliant, but used by some tools like Elasticsearch
    get_payload = ("GET", "/api/get_payload")
    multiple_failures = ("GET", "/api/multiple_failures")
    slow = ("GET", "/api/slow")
    path_variable = ("GET", "/api/path_variable/{key}")
    unsatisfiable = ("POST", "/api/unsatisfiable")
    performance = ("POST", "/api/performance")
    invalid = ("POST", "/api/invalid")
    flaky = ("GET", "/api/flaky")
    recursive = ("GET", "/api/recursive")
    multipart = ("POST", "/api/multipart")
    upload_file = ("POST", "/api/upload_file")
    form = ("POST", "/api/form")
    teapot = ("POST", "/api/teapot")
    text = ("GET", "/api/text")
    plain_text_body = ("POST", "/api/text")
    csv_payload = ("POST", "/api/csv")
    malformed_json = ("GET", "/api/malformed_json")
    invalid_response = ("GET", "/api/invalid_response")
    custom_format = ("GET", "/api/custom_format")
    invalid_path_parameter = ("GET", "/api/invalid_path_parameter/{id}")
    missing_path_parameter = ("GET", "/api/missing_path_parameter/{id}")
    headers = ("GET", "/api/headers")

    create_user = ("POST", "/api/users/")
    get_user = ("GET", "/api/users/{user_id}")
    update_user = ("PATCH", "/api/users/{user_id}")
    all = object()


class OpenAPIVersion(Enum):
    _2 = "2.0"
    _3 = "3.0"

    @property
    def is_openapi_2(self):
        return self.value == "2.0"

    @property
    def is_openapi_3(self):
        return self.value == "3.0"


def make_openapi_schema(endpoints: Tuple[str, ...], version: OpenAPIVersion = OpenAPIVersion("2.0")) -> Dict:
    """Generate an OAS 2/3 schemas with the given endpoints.

    Example:
        If `endpoints` is ("success", "failure")
        then the app will contain GET /success and GET /failure

    """
    return {OpenAPIVersion("2.0"): _make_openapi_2_schema, OpenAPIVersion("3.0"): _make_openapi_3_schema}[version](
        endpoints
    )


def make_node_definition(reference):
    return {
        "description": "Recursive!",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "children": {"type": "array", "items": reference},
            "value": {"type": "integer", "maximum": 4, "exclusiveMaximum": True},
        },
    }


PAYLOAD = {
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

PAYLOAD_VALIDATOR = jsonschema.validators.Draft4Validator({"anyOf": [{"type": "null"}, PAYLOAD]})


def _make_openapi_2_schema(endpoints: Tuple[str, ...]) -> Dict:
    template: Dict[str, Any] = {
        "swagger": "2.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/api",
        "schemes": ["http"],
        "produces": ["application/json"],
        "paths": {},
        "securityDefinitions": {"api_key": {"type": "apiKey", "name": "X-Token", "in": "header"}},
    }

    def add_link(name, definition):
        components = template.setdefault("x-components", {})
        links = components.setdefault("x-links", {})
        links.setdefault(name, definition)

    for endpoint in endpoints:
        method, path = Endpoint[endpoint].value
        path = path.replace(template["basePath"], "")
        reference = {"$ref": "#/definitions/Node"}
        if endpoint == "recursive":
            schema = {"responses": {"200": {"description": "OK", "schema": reference}}}
            definitions = template.setdefault("definitions", {})
            definitions["Node"] = make_node_definition(reference)
        elif endpoint in ("payload", "get_payload"):
            schema = {
                "parameters": [{"name": "body", "in": "body", "required": True, "schema": PAYLOAD}],
                "responses": {"200": {"description": "OK", "schema": PAYLOAD}},
            }
        elif endpoint == "unsatisfiable":
            schema = {
                "parameters": [
                    {
                        "name": "id",
                        "in": "body",
                        "required": True,
                        # Impossible to satisfy
                        "schema": {"allOf": [{"type": "integer"}, {"type": "string"}]},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "performance":
            schema = {
                "parameters": [{"name": "data", "in": "body", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint in ("flaky", "multiple_failures"):
            schema = {
                "parameters": [{"name": "id", "in": "query", "required": True, "type": "integer"}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "path_variable":
            schema = {
                "parameters": [{"name": "key", "in": "path", "required": True, "type": "string", "minLength": 1}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "invalid":
            schema = {
                "parameters": [{"name": "id", "in": "query", "required": True, "type": "int"}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "upload_file":
            schema = {
                "parameters": [
                    {"name": "note", "in": "formData", "required": True, "type": "string"},
                    {"name": "data", "in": "formData", "required": True, "type": "file"},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "form":
            schema = {
                "parameters": [
                    {"name": "first_name", "in": "formData", "required": True, "type": "string"},
                    {"name": "last_name", "in": "formData", "required": True, "type": "string"},
                ],
                "consumes": ["application/x-www-form-urlencoded"],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "custom_format":
            schema = {
                "parameters": [{"name": "id", "in": "query", "required": True, "type": "string", "format": "digits"}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "multipart":
            schema = {
                "parameters": [
                    {"in": "formData", "name": "key", "required": True, "type": "string"},
                    {"in": "formData", "name": "value", "required": True, "type": "integer"},
                    {"in": "formData", "name": "maybe", "type": "boolean"},
                ],
                "consumes": ["multipart/form-data"],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "teapot":
            schema = {"produces": ["application/json"], "responses": {"200": {"description": "OK"}}}
        elif endpoint == "plain_text_body":
            schema = {
                "parameters": [
                    {"in": "body", "name": "value", "required": True, "schema": {"type": "string"}},
                ],
                "consumes": ["text/plain"],
                "produces": ["text/plain"],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "invalid_path_parameter":
            schema = {
                "parameters": [{"name": "id", "in": "path", "required": False, "type": "integer"}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "headers":
            schema = {
                "security": [{"api_key": []}],
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"type": "object"},
                        "headers": {"X-Custom-Header": {"description": "Custom header", "type": "integer"}},
                    },
                    "default": {"description": "Default response"},
                },
            }
        elif endpoint == "create_user":
            schema = {
                "parameters": [
                    {
                        "name": "data",
                        "in": "body",
                        "required": True,
                        "schema": {
                            "type": "object",
                            "properties": {"username": {"type": "string", "minLength": 3}},
                            "required": ["username"],
                            "additionalProperties": False,
                        },
                    }
                ],
                "responses": {"201": {"$ref": "#/x-components/responses/ResponseWithLinks"}},
            }
            add_link(
                "UpdateUserById",
                {
                    "operationId": "updateUser",
                    "parameters": {"user_id": "$response.body#/id"},
                    "requestBody": {"username": "foo"},
                },
            )
            template["x-components"]["responses"] = {
                "ResponseWithLinks": {
                    "description": "OK",
                    "x-links": {
                        "GetUserByUserId": {
                            "operationId": "getUser",
                            "parameters": {
                                "path.user_id": "$response.body#/id",
                                "query.user_id": "$response.body#/id",
                            },
                        },
                        "UpdateUserById": {"$ref": "#/x-components/x-links/UpdateUserById"},
                    },
                }
            }
        elif endpoint == "get_user":
            parent = template["paths"].setdefault(path, {})
            parent["parameters"] = [{"in": "path", "name": "user_id", "required": True, "type": "integer"}]
            schema = {
                "operationId": "getUser",
                "parameters": [
                    {"in": "query", "name": "code", "required": True, "type": "integer"},
                    {"in": "query", "name": "user_id", "required": True, "type": "integer"},
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "x-links": {
                            "UpdateUserById": {
                                "operationRef": "#/paths/~1users~1{user_id}/patch",
                                "parameters": {"user_id": "$response.body#/id"},
                                "requestBody": {"username": "foo"},
                            }
                        },
                    },
                    "404": {"description": "Not found"},
                },
            }
        elif endpoint == "update_user":
            parent = template["paths"].setdefault(path, {})
            parent["parameters"] = [
                {"in": "path", "name": "user_id", "required": True, "type": "integer"},
                {"in": "query", "name": "common", "required": True, "type": "integer"},
            ]
            schema = {
                "operationId": "updateUser",
                "parameters": [
                    {
                        "in": "body",
                        "name": "username",
                        "required": True,
                        "schema": {
                            "additionalProperties": False,
                            "type": "object",
                            "properties": {"username": {"type": "string"}},
                            "required": ["username"],
                        },
                    },
                ],
                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
            }
        elif endpoint == "csv_payload":
            schema = {
                "parameters": [
                    {
                        "in": "body",
                        "name": "payload",
                        "required": True,
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
                        },
                    },
                ],
                "consumes": ["text/csv"],
                "responses": {"200": {"description": "OK"}},
            }
        else:
            schema = {
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {
                            "type": "object",
                            "properties": {"success": {"type": "boolean"}},
                            "required": ["success"],
                        },
                    },
                    "default": {"description": "Default response"},
                }
            }
        template["paths"].setdefault(path, {})
        template["paths"][path][method.lower()] = schema
    return template


def _make_openapi_3_schema(endpoints: Tuple[str, ...]) -> Dict:
    _base_path = "api"
    template: Dict[str, Any] = {
        "openapi": "3.0.2",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "paths": {},
        "servers": [{"url": "https://127.0.0.1:8888/{basePath}", "variables": {"basePath": {"default": _base_path}}}],
    }
    base_path = f"/{_base_path}"

    def add_link(name, definition):
        components = template.setdefault("components", {})
        links = components.setdefault("links", {})
        links.setdefault(name, definition)

    for endpoint in endpoints:
        method, path = Endpoint[endpoint].value
        path = path.replace(base_path, "")
        reference = {"$ref": "#/x-definitions/Node"}
        if endpoint == "recursive":
            schema = {
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": reference}}}}
            }
            definitions = template.setdefault("x-definitions", {})
            definitions["Node"] = make_node_definition(reference)
        elif endpoint in ("payload", "get_payload"):
            schema = {
                "requestBody": {"content": {"application/json": {"schema": PAYLOAD}}},
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": PAYLOAD}}}},
            }
        elif endpoint == "unsatisfiable":
            schema = {
                "requestBody": {
                    "content": {"application/json": {"schema": {"allOf": [{"type": "integer"}, {"type": "string"}]}}},
                    "required": True,
                },
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "performance":
            schema = {
                "requestBody": {"content": {"application/json": {"schema": {"type": "integer"}}}, "required": True},
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "plain_text_body":
            schema = {
                "requestBody": {"content": {"text/plain": {"schema": {"type": "string"}}}, "required": True},
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint in ("flaky", "multiple_failures"):
            schema = {
                "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "path_variable":
            schema = {
                "parameters": [
                    {"name": "key", "in": "path", "required": True, "schema": {"type": "string", "minLength": 1}}
                ],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "invalid":
            schema = {
                "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "int"}}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "upload_file":
            schema = {
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "data": {"type": "string", "format": "binary"},
                                    "note": {"type": "string"},
                                },
                                "required": ["data", "note"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "form":
            schema = {
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
        elif endpoint == "custom_format":
            schema = {
                "parameters": [
                    {"name": "id", "in": "query", "required": True, "schema": {"type": "string", "format": "digits"}}
                ],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "multipart":
            schema = {
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
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "teapot":
            schema = {
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
        elif endpoint == "invalid_path_parameter":
            schema = {
                "parameters": [{"name": "id", "in": "path", "required": False, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "headers":
            schema = {
                "security": [{"api_key": []}],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                        "headers": {"X-Custom-Header": {"description": "Custom header", "schema": {"type": "integer"}}},
                    },
                    "default": {"description": "Default response"},
                },
            }
        elif endpoint == "create_user":
            schema = {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"username": {"type": "string", "minLength": 3}},
                                "required": ["username"],
                                "additionalProperties": False,
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {"201": {"$ref": "#/components/responses/ResponseWithLinks"}},
            }
            add_link(
                "UpdateUserById",
                {
                    "operationId": "updateUser",
                    "parameters": {"user_id": "$response.body#/id"},
                    "requestBody": {"username": "foo"},
                },
            )
            template["components"]["responses"] = {
                "ResponseWithLinks": {
                    "description": "OK",
                    "links": {
                        "GetUserByUserId": {
                            "operationId": "getUser",
                            "parameters": {
                                "path.user_id": "$response.body#/id",
                                "query.user_id": "$response.body#/id",
                            },
                        },
                        "UpdateUserById": {"$ref": "#/components/links/UpdateUserById"},
                    },
                }
            }
        elif endpoint == "get_user":
            parent = template["paths"].setdefault(path, {})
            parent["parameters"] = [{"in": "path", "name": "user_id", "required": True, "schema": {"type": "integer"}}]
            schema = {
                "operationId": "getUser",
                "parameters": [
                    {"in": "query", "name": "code", "required": True, "schema": {"type": "integer"}},
                    {"in": "query", "name": "user_id", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "links": {
                            "UpdateUserById": {
                                "operationRef": "#/paths/~1users~1{user_id}/patch",
                                "parameters": {"user_id": "$response.body#/id"},
                                "requestBody": {"username": "foo"},
                            }
                        },
                    },
                    "404": {"description": "Not found"},
                },
            }
        elif endpoint == "update_user":
            parent = template["paths"].setdefault(path, {})
            parent["parameters"] = [
                {"in": "path", "name": "user_id", "required": True, "schema": {"type": "integer"}},
                {"in": "query", "name": "common", "required": True, "schema": {"type": "integer"}},
            ]
            schema = {
                "operationId": "updateUser",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "additionalProperties": False,
                                "type": "object",
                                "properties": {"username": {"type": "string"}},
                                "required": ["username"],
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
            }
        elif endpoint == "csv_payload":
            schema = {
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
        else:
            schema = {
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
                    },
                    "default": {"description": "Default response", "content": {"application/json": {"schema": {}}}},
                }
            }
        template["paths"].setdefault(path, {})
        template["paths"][path][method.lower()] = schema
        template["paths"].setdefault(path, {})
        template["paths"][path][method.lower()] = schema
    return template
