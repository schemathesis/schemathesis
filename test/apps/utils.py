from enum import Enum
from typing import Any, Dict, Tuple


class Endpoint(Enum):
    success = ("GET", "/api/success")
    failure = ("GET", "/api/failure")
    payload = ("POST", "/api/payload")
    multiple_failures = ("GET", "/api/multiple_failures")
    slow = ("GET", "/api/slow")
    path_variable = ("GET", "/api/path_variable/{key}")
    unsatisfiable = ("POST", "/api/unsatisfiable")
    invalid = ("POST", "/api/invalid")
    flaky = ("GET", "/api/flaky")
    multipart = ("POST", "/api/multipart")
    upload_file = ("POST", "/api/upload_file")
    teapot = ("POST", "/api/teapot")
    text = ("GET", "/api/text")
    malformed_json = ("GET", "/api/malformed_json")
    invalid_response = ("GET", "/api/invalid_response")
    custom_format = ("GET", "/api/custom_format")
    invalid_path_parameter = ("GET", "/api/invalid_path_parameter/{id}")


def make_schema(endpoints: Tuple[str, ...]) -> Dict:
    """Generate a Swagger 2.0 schema with the given endpoints.

    Example:
        If `endpoints` is ("success", "failure")
        then the app will contain GET /success and GET /failure

    """
    template: Dict[str, Any] = {
        "swagger": "2.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/api",
        "schemes": ["http"],
        "produces": ["application/json"],
        "paths": {},
    }
    for endpoint in endpoints:
        method, path = Endpoint[endpoint].value
        path = path.replace(template["basePath"], "")
        if endpoint == "payload":
            schema = {
                "parameters": [
                    {
                        "name": "body",
                        "in": "body",
                        "required": True,
                        "schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                            "example": {"name": "John"},
                        },
                    }
                ],
                "responses": {"200": {"description": "OK"}},
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
                "parameters": [{"name": "data", "in": "formData", "required": True, "type": "file"}],
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
                ],
                "responses": {"200": {"description": "OK"}},
            }
        elif endpoint == "teapot":
            schema = {"produces": ["application/json"], "responses": {"200": {"description": "OK"}}}
        elif endpoint == "invalid_path_parameter/{id}":
            schema = {
                "parameters": [{"name": "id", "in": "path", "required": False, "type": "integer"}],
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
        template["paths"][path] = {method.lower(): schema}
    return template
