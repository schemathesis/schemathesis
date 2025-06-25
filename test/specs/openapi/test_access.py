import json
from dataclasses import asdict
from pathlib import Path

import pytest
from referencing import Resource
from referencing.exceptions import NoSuchResource

from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.core.result import Err, Ok
from schemathesis.specs.openapi._access import OpenApi
from schemathesis.specs.openapi.definitions import OPENAPI_30_VALIDATOR, OPENAPI_31_VALIDATOR, SWAGGER_20_VALIDATOR

HERE = Path(__file__).parent.absolute()
SCHEMAS_DIR = HERE / "schemas"


def read_schema(name):
    with open(SCHEMAS_DIR / name) as fd:
        return json.load(fd)


@pytest.fixture
def schema(request):
    schema = read_schema(request.param)
    if "swagger" in schema:
        SWAGGER_20_VALIDATOR.validate(schema)
    elif schema["openapi"].startswith("3.0"):
        OPENAPI_30_VALIDATOR.validate(schema)
    else:
        OPENAPI_31_VALIDATOR.validate(schema)

    return OpenApi(schema, retrieve=retrieve)


def retrieve(uri: str):
    if uri == "paths.json":
        contents = {
            "UsersPaths": {
                "parameters": [{"$ref": "parameters.json#/Q"}],
                "get": {"responses": {"200": {"description": "Ok"}}},
                "post": {
                    "parameters": [
                        {
                            "name": "user",
                            "in": "body",
                            "required": True,
                            "schema": {"type": "object"},
                        }
                    ],
                    "responses": {"201": {"description": "Ok"}},
                },
            },
        }
    elif uri == "parameters.json":
        contents = {
            "Q": {
                "name": "q",
                "in": "query",
                "required": True,
                "type": "string",
            },
        }
    else:
        raise NoSuchResource(ref=uri)
    return Resource.opaque(contents)


@pytest.mark.parametrize("schema", SCHEMAS_DIR.iterdir(), ids=lambda x: x.name, indirect=True)
def test_operations(schema, snapshot_json):
    for operation in schema:
        if isinstance(operation, Ok):
            assert asdict(operation.ok()) == snapshot_json
        else:
            error = operation.err()
            assert {
                "error": {
                    "message": error.message,
                    "path": error.path,
                    "method": error.method,
                }
            } == snapshot_json


def test_invalid_path_item(snapshot_json):
    interface = OpenApi(
        {
            "swagger": "2.0",
            "paths": {
                "/users": "invalid",
            },
        }
    )
    items = list(interface)
    assert len(items) == 1
    assert isinstance(items[0], Err)
    error = items[0].err()
    assert {
        "error": {
            "message": error.message,
            "path": error.path,
            "method": error.method,
        }
    } == snapshot_json


def test_access_openapi_3():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "version", "in": "header", "schema": {"type": "string"}},
                ],
                "get": {
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "sessionId", "in": "cookie", "schema": {"type": "string"}},
                    ],
                    "tags": ["users"],
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            },
                            "application/xml": {"schema": {"type": "string"}},
                        }
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            }
        },
    }

    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)

    assert schema.title == raw_schema["info"]["title"]
    assert schema.version == raw_schema["info"]["version"]

    raw_users = raw_schema["paths"]["/users/{id}"]

    get_users = schema["/users/{id}"]["GET"]
    assert [p.definition for p in get_users.query] == [raw_users["get"]["parameters"][0]]
    assert [p.definition for p in get_users.path_parameters] == [raw_users["parameters"][0]]
    assert [p.definition for p in get_users.headers] == [raw_users["parameters"][1]]
    assert [p.definition for p in get_users.cookies] == [raw_users["get"]["parameters"][1]]
    assert list(get_users.body) == []
    assert get_users.tags == ["users"]

    post_users = schema["/users/{id}"]["POST"]
    assert list(post_users.query) == []
    assert list(post_users.cookies) == []
    body_params = [b.definition for b in post_users.body]
    assert body_params == [
        raw_users["post"]["requestBody"]["content"]["application/json"],
        raw_users["post"]["requestBody"]["content"]["application/xml"],
    ]
    assert post_users.tags is None


def test_access_swagger_2():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "type": "integer"},
                ],
                "post": {
                    "parameters": [
                        # Likely won't happen in reality if `body` & `formData` are present simultaneously,
                        # but it is simpler to test it this way
                        {"name": "user", "in": "body", "required": True, "schema": {"type": "object"}},
                        {"name": "file", "in": "formData", "type": "file"},
                        {"name": "name", "in": "formData", "type": "string"},
                    ],
                    "responses": {"201": {"description": "Created"}},
                },
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/users/{id}"]["POST"]

    assert [p.definition for p in operation.path_parameters] == [raw_schema["paths"]["/users/{id}"]["parameters"][0]]

    # Body should contain both body param and composite formData
    bodies = [b.definition for b in operation.body]
    assert len(bodies) == 2
    # First should be the body parameter
    assert bodies[0] == raw_schema["paths"]["/users/{id}"]["post"]["parameters"][0]
    # Second should be composite formData parameters (list)
    assert bodies[1] == raw_schema["paths"]["/users/{id}"]["post"]["parameters"][1:3]


def test_parameter_references():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "components": {
            "parameters": {
                "QueryParam": {"name": "q", "in": "query", "schema": {"type": "string"}},
                "HeaderParam": {"name": "auth", "in": "header", "schema": {"type": "string"}},
            }
        },
        "paths": {
            "/items": {
                "parameters": [{"$ref": "#/components/parameters/HeaderParam"}],
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/QueryParam"}],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/items"]["GET"]

    # References should be resolved
    assert [p.definition for p in operation.query] == [raw_schema["components"]["parameters"]["QueryParam"]]
    assert [p.definition for p in operation.headers] == [raw_schema["components"]["parameters"]["HeaderParam"]]


def test_swagger_2_media_types():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "consumes": ["application/xml", "text/plain"],
        "paths": {
            "/data": {
                "post": {
                    "parameters": [
                        {"name": "payload", "in": "body", "schema": {"type": "string"}},
                    ],
                    "consumes": ["application/json"],  # Override global
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/form": {
                "post": {
                    "parameters": [
                        {"name": "field", "in": "formData", "type": "string"},
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }

    schema = OpenApi(raw_schema)

    # Body operation should use operation-level consumes
    data = list(schema["/data"]["POST"].body)
    assert len(data) == 1
    assert data[0].media_type == "application/json"

    # Form operation should use global consumes for formData
    form_bodies = list(schema["/form"]["POST"].body)
    assert len(form_bodies) == 2
    assert {b.media_type for b in form_bodies} == {"application/xml", "text/plain"}


def test_openapi_3_body_references():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "components": {
            "requestBodies": {
                "UserBody": {
                    "content": {
                        "application/json": {"schema": {"type": "object"}},
                    }
                }
            },
            "schemas": {"UserSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
        },
        "paths": {
            "/users": {
                "post": {
                    "requestBody": {"$ref": "#/components/requestBodies/UserBody"},
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/profiles": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"$ref": "#/components/schemas/UserSchema"},
                        }
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            },
        },
    }

    schema = OpenApi(raw_schema)

    # RequestBody reference should be resolved
    users = list(schema["/users"]["POST"].body)
    assert len(users) == 1
    assert users[0].definition == {"schema": {"type": "object"}}

    # Content reference should be resolved
    profiles = list(schema["/profiles"]["POST"].body)
    assert len(profiles) == 1
    assert profiles[0].definition == raw_schema["components"]["schemas"]["UserSchema"]


def test_parameter_override():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/items/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "version", "in": "header", "schema": {"type": "string", "default": "v1"}},
                    {"name": "global-query", "in": "query", "schema": {"type": "string"}},
                ],
                "get": {
                    "parameters": [
                        # Override the header parameter (same name + location)
                        {"name": "version", "in": "header", "required": True, "schema": {"type": "string"}},
                        {"name": "local-query", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/items/{id}"]["GET"]

    # Path parameters should include inherited path-level parameter
    path_params = [p.definition for p in operation.path_parameters]
    assert path_params == [raw_schema["paths"]["/items/{id}"]["parameters"][0]]

    # Headers should have the operation-level version (overriding path-level)
    headers = [p.definition for p in operation.headers]
    assert len(headers) == 1
    assert headers[0] == raw_schema["paths"]["/items/{id}"]["get"]["parameters"][0]
    assert headers[0]["required"] is True  # Operation-level override

    # Query should have both inherited and operation-level parameters
    queries = [p.definition for p in operation.query]
    assert len(queries) == 2
    query_names = {q["name"] for q in queries}
    assert query_names == {"global-query", "local-query"}


def test_empty_cases():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/simple": {
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/empty-body": {
                "post": {
                    "requestBody": {"content": {}},  # Empty content
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }

    schema = OpenApi(raw_schema)

    # Simple operation should have no parameters
    simple = schema["/simple"]["GET"]
    assert list(simple.query) == []
    assert list(simple.path_parameters) == []
    assert list(simple.headers) == []
    assert list(simple.cookies) == []
    assert list(simple.body) == []

    # Empty body content should yield nothing
    assert list(schema["/empty-body"]["POST"].body) == []


def test_invalid_request_body():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/broken": {
                "post": {
                    "requestBody": {},  # Missing required 'content' key
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }

    schema = OpenApi(raw_schema)
    operation = next(op.ok() for op in schema if isinstance(op, Ok))

    with pytest.raises(InvalidSchema, match="Missing required key `content`"):
        list(operation.body)


def test_mapping_interface_no_cache():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"201": {"description": "Created"}}},
            },
            "/users/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "get": {"responses": {"200": {"description": "OK"}}},
            },
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)

    get_users_1 = schema["/users"]["GET"]
    get_users_2 = schema["/users"]["GET"]

    assert get_users_1.label == get_users_2.label == "GET /users"

    # Test parameter inheritance works
    path_params = list(schema["/users/{id}"]["GET"].path_parameters)
    assert len(path_params) == 1
    assert path_params[0].definition["name"] == "id"

    params = schema["/users/{id}"]["GET"].path_parameters
    assert "id" in params
    assert "id" in params  # cached
    assert list(params) == list(params)

    # Test path operations interface
    users = schema["/users"]
    assert "GET" in users
    assert "POST" in users
    assert "DELETE" not in users
    assert "WHATEVER" not in users
    assert list(users) == ["GET", "POST"]

    with pytest.raises(OperationNotFound, match="`/nonexistent` not found"):
        schema["/nonexistent"]

    with pytest.raises(OperationNotFound, match="`/userz` not found. Did you mean `/users`?"):
        schema["/userz"]

    with pytest.raises(LookupError, match="Method `DELETE` not found. Available methods: GET, POST"):
        schema["/users"]["DELETE"]

    with pytest.raises(KeyError, match="Invalid HTTP method 'WHATEVER'"):
        schema["/users"]["WHATEVER"]


def test_response_access_openapi_3():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                            "headers": {"X-Rate-Limit": {"schema": {"type": "integer"}}},
                        },
                        "404": {"description": "Not found", "content": {"text/plain": {"schema": {"type": "string"}}}},
                    }
                }
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]
    responses = operation.responses

    # Should have both status codes
    assert set(responses.keys()) == {"200", "404"}

    # Test 200 response
    assert responses["200"].schema == {"type": "object", "properties": {"id": {"type": "integer"}}}
    assert responses["200"].headers == {"X-Rate-Limit": {"schema": {"type": "integer"}}}

    # Test 404 response
    assert responses["404"].schema == {"type": "string"}
    assert responses["404"].headers is None


def test_response_access_swagger_2():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "schema": {"type": "array", "items": {"type": "object"}},
                            "headers": {"X-Total-Count": {"type": "integer"}},
                        },
                        "400": {
                            "description": "Bad request"
                            # No schema
                        },
                    }
                }
            }
        },
    }
    SWAGGER_20_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]
    responses = operation.responses

    # Test 200 response with schema
    assert responses["200"].schema == {"type": "array", "items": {"type": "object"}}
    assert responses["200"].headers == {"X-Total-Count": {"type": "integer"}}

    # Test 400 response without schema
    assert responses["400"].schema is None
    assert responses["400"].headers is None


def test_response_references():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "components": {
            "responses": {
                "ErrorResponse": {
                    "description": "Error",
                    "content": {
                        "application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}
                    },
                }
            }
        },
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                        "500": {"$ref": "#/components/responses/ErrorResponse"},
                    }
                }
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]
    responses = operation.responses

    # Referenced response should be resolved
    assert responses["500"].schema == {"type": "object", "properties": {"error": {"type": "string"}}}


def test_openapi_3_response_edge_cases():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/test": {
                "get": {
                    "responses": {
                        "200": {"description": "Empty content", "content": {}},
                        "201": {
                            "description": "No content key"
                            # Missing content entirely
                        },
                        "202": {
                            "description": "Multiple content types",
                            "content": {
                                "application/json": {"schema": {"type": "object"}},
                                "application/xml": {"schema": {"type": "string"}},
                            },
                        },
                    }
                }
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/test"]["GET"]
    responses = operation.responses

    # Empty content should return None
    assert responses["200"].schema is None

    # Missing content should return None
    assert responses["201"].schema is None

    # Multiple content types should return first one
    assert responses["202"].schema == {"type": "object"}


def test_response_status_codes():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/test": {
                "get": {
                    "responses": {
                        200: {  # Integer status code
                            "description": "Success",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                        "4XX": {  # Wildcard status code
                            "description": "Client error",
                            "content": {"application/json": {"schema": {"type": "string"}}},
                        },
                        "default": {  # Default response
                            "description": "Other",
                            "content": {"application/json": {"schema": {"type": "null"}}},
                        },
                    }
                }
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/test"]["GET"]
    responses = operation.responses

    # All status codes should be converted to strings
    assert set(responses.keys()) == {"200", "4XX", "default"}

    # Each should have correct schema
    assert responses["200"].schema == {"type": "object"}
    assert responses["4XX"].schema == {"type": "string"}
    assert responses["default"].schema == {"type": "null"}


def test_no_responses():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/test": {
                "get": {
                    # No responses defined
                }
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/test"]["GET"]

    # Should return empty dict when no responses
    assert operation.responses == {}


def test_failed_shared_parameter_reference():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "parameters": [
                    {"$ref": "#/components/parameters/NonExistent"}  # Invalid reference
                ],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }

    schema = OpenApi(raw_schema)

    # Should raise InvalidSchema when trying to access the operation
    with pytest.raises(InvalidSchema, match="Failed to resolve reference"):
        schema["/users"]["GET"]


def test_failed_path_item_reference():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {"$ref": "#/paths/NonExistentPath"}  # Invalid path item reference
        },
    }

    schema = OpenApi(raw_schema)

    # Should raise InvalidSchema when trying to access any operation
    with pytest.raises(InvalidSchema, match="Failed to resolve reference"):
        schema["/users"]["GET"]

    # Should also fail when checking if method exists
    with pytest.raises(InvalidSchema, match="Failed to resolve reference"):
        _ = "GET" in schema["/users"]

    # Should also fail when iterating methods
    with pytest.raises(InvalidSchema, match="Failed to resolve reference"):
        list(schema["/users"])


def test_path_item_not_dict_mapping():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": "invalid_path_item"  # Should be dict, not string
        },
    }

    schema = OpenApi(raw_schema)

    # Should raise InvalidSchema when accessing via mapping interface
    with pytest.raises(InvalidSchema, match="Path item should be an object, got str: invalid_path_item"):
        schema["/users"]

    # But path should still be considered to exist for __contains__
    assert "/users" not in schema


def test_iter_parameters():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "parameters": [
                    {"name": "version", "in": "header", "type": "string"},
                    {"name": "id", "in": "path", "required": True, "type": "integer"},
                ],
                "post": {
                    "parameters": [
                        {"name": "q", "in": "query", "type": "string"},
                        {"name": "auth", "in": "header", "type": "string"},
                        {"name": "session", "in": "cookie", "type": "string"},
                        {"name": "user", "in": "body", "schema": {"type": "object"}},
                        {"name": "file", "in": "formData", "type": "file"},
                    ],
                    "responses": {"201": {"description": "Created"}},
                },
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["POST"]

    # iter_parameters should exclude body and formData parameters
    filtered_params = list(operation.parameters)
    param_locations = [p.location for p in filtered_params]
    param_names = [p.definition["name"] for p in filtered_params]

    # Should have query, path, header, cookie - but NOT body or formData
    assert set(param_locations) == {"query", "path", "header", "cookie"}
    assert set(param_names) == {"q", "id", "version", "auth", "session"}

    # Verify body and formData are excluded
    all_params = list(operation._iter_parameters())
    all_locations = [p.location for p in all_params]
    assert "body" in all_locations
    assert "formData" in all_locations
    assert len(all_params) == len(filtered_params) + 2  # +2 for body and formData


def test_response_examples_swagger_2_with_names():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "schema": {"type": "object"},
                            "examples": {
                                "application/json": {"id": 1, "name": "John"},
                                "application/xml": "<user><id>1</id></user>",
                            },
                        }
                    }
                }
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]
    response = operation.responses["200"]

    examples = list(response.examples)
    assert len(examples) == 2

    # Check names and values
    example_dict = {ex.name: ex.value for ex in examples}
    assert example_dict["application/json"] == {"id": 1, "name": "John"}
    assert example_dict["application/xml"] == "<user><id>1</id></user>"


def test_response_examples_openapi_3_with_names():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"},
                                    "example": {"id": 1, "name": "John"},
                                    "examples": {
                                        "user1": {"value": {"id": 1, "name": "John"}},
                                        "user2": {"value": {"id": 2, "name": "Jane"}},
                                    },
                                },
                                "text/plain": {
                                    "schema": {"type": "string"},
                                    "x-example": "Simple text",
                                    "x-examples": {
                                        "ex1": {"value": "Text 1"},
                                        "ex2": "Text 2",  # Direct value
                                    },
                                },
                            },
                        }
                    }
                }
            }
        },
    }

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]
    response = operation.responses["200"]

    examples = {}
    for example in response.examples:
        examples.setdefault(example.name, []).append(example.value)

    assert examples == {
        "200/application/json": [{"id": 1, "name": "John"}, {"id": 2, "name": "Jane"}, {"id": 1, "name": "John"}],
        "200/text/plain": ["Text 1", "Simple text"],
    }


def test_response_examples_naming_scheme():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "components": {"schemas": {"Foo": {"type": "integer"}}},
        "paths": {
            "/test": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "example": "single_example",
                                    "examples": {"named_example": {"value": "from_examples"}},
                                    "x-example": "extension_example",
                                    "x-examples": {"ext_named": {"value": "from_x_examples"}},
                                },
                                "text/plain": {
                                    "schema": {
                                        "$ref": "#/components/schemas/Foo",
                                    },
                                    "example": "single_example",
                                },
                            },
                        }
                    }
                }
            }
        },
    }

    schema = OpenApi(raw_schema)
    response = schema["/test"]["GET"].responses["200"]

    examples = {}
    for example in response.examples:
        examples.setdefault(example.name, []).append(example.value)

    assert examples == {
        "200/application/json": ["from_examples", "single_example", "from_x_examples", "extension_example"],
        "Foo": ["single_example"],
    }


def test_content_types_swagger_2():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "produces": ["application/json", "application/xml"],  # Global produces
        "paths": {
            "/users": {
                "get": {
                    "produces": ["text/plain"],  # Operation-level override
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    # No operation-level produces - should use global
                    "responses": {"201": {"description": "Created"}}
                },
            }
        },
    }

    schema = OpenApi(raw_schema)

    # Operation with specific produces
    get_op = schema["/users"]["GET"]
    assert get_op.output_content_types_for(200) == ["text/plain"]

    # Operation using global produces
    post_op = schema["/users"]["POST"]
    assert post_op.output_content_types_for(201) == ["application/json", "application/xml"]


def test_content_types_swagger_2_no_global():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        # No global produces
        "paths": {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]

    # Should return empty list when no produces defined
    assert operation.output_content_types_for(200) == []


def test_content_types_openapi_3():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {"schema": {"type": "object"}},
                                "application/xml": {"schema": {"type": "object"}},
                                "text/csv": {"schema": {"type": "string"}},
                            },
                        },
                        "400": {
                            "description": "Error",
                            "content": {"application/problem+json": {"schema": {"type": "object"}}},
                        },
                        "404": {
                            "description": "Not found"
                            # No content
                        },
                    }
                }
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]

    # Multiple content types
    assert operation.output_content_types_for(200) == ["application/json", "application/xml", "text/csv"]

    # Single content type
    assert operation.output_content_types_for(400) == ["application/problem+json"]

    # No content
    assert operation.output_content_types_for(404) == []

    # Not defined
    assert operation.output_content_types_for(422) == []


def test_content_types_default_response():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                        "default": {
                            "description": "Error",
                            "content": {"application/problem+json": {"schema": {"type": "object"}}},
                        },
                    }
                }
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]

    # Specific status code
    assert operation.output_content_types_for(200) == ["application/json"]

    # Non-existent status code should use default
    assert operation.output_content_types_for(500) == ["application/problem+json"]

    # Another non-existent should also use default
    assert operation.output_content_types_for(404) == ["application/problem+json"]


def test_parameter_examples_openapi_3(server):
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "components": {
            "examples": {
                "RefExample": {"value": "referenced_value"},
                "ExternalRef": {"externalValue": f"http://127.0.0.1:{server['port']}/answer.json"},
            },
        },
        "paths": {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "basic",
                            "in": "query",
                            "schema": {"type": "string"},
                            "example": "single_value",
                        },
                        {
                            "name": "multiple",
                            "in": "query",
                            "schema": {"type": "string"},
                            "examples": {
                                "direct": {"value": "direct_value"},
                                "referenced": {"$ref": "#/components/examples/RefExample"},
                                "external": {"$ref": "#/components/examples/ExternalRef"},
                            },
                        },
                        {
                            "name": "content_single",
                            "in": "query",
                            "content": {"application/json": {"schema": {"type": "object"}, "example": "content_ex1"}},
                        },
                        {
                            "name": "content_multiple",
                            "in": "query",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"},
                                    "examples": {
                                        "content_ex1": {"value": "ex1"},
                                        "content_ex2": {"value": "ex2"},
                                    },
                                }
                            },
                        },
                        {
                            "name": "schema_any_of",
                            "in": "query",
                            "schema": {
                                "anyOf": [
                                    {"type": "string", "example": 1},
                                    {"type": "integer", "example": 2},
                                    {
                                        "type": "object",
                                        "allOf": [
                                            {"properties": {"base": {"type": "string"}}, "example": {"base": "nested"}},
                                            {
                                                "properties": {"extra": {"type": "string"}},
                                                "example": {"extra": "addon"},
                                            },
                                        ],
                                    },
                                ]
                            },
                        },
                        {
                            "name": "schema_all_of",
                            "in": "query",
                            "schema": {
                                "type": "object",
                                "allOf": [
                                    {"properties": {"base": {"type": "string"}}, "example": {"base": "nested"}},
                                    {
                                        "properties": {"extra": {"type": "string"}},
                                        "example": {"extra": "addon"},
                                    },
                                    {
                                        "properties": {"more": {"type": "string"}},
                                        "examples": [{"more": "A"}, {"more": "B"}],
                                    },
                                ],
                            },
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    OPENAPI_31_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["GET"]

    assert {ex.name: ex.value for ex in operation.query["basic"].examples} == {"example_0": "single_value"}
    assert {ex.name: ex.value for ex in operation.query["multiple"].examples} == {
        "direct_0": "direct_value",
        "external_2": b"42",
        "referenced_1": "referenced_value",
    }
    assert {ex.name: ex.value for ex in operation.query["content_single"].examples} == {"example_0": "content_ex1"}
    assert {ex.name: ex.value for ex in operation.query["content_multiple"].examples} == {
        "content_ex1_0": "ex1",
        "content_ex2_1": "ex2",
    }
    assert {ex.name: ex.value for ex in operation.query["schema_any_of"].examples} == {
        "example_0": 1,
        "example_1": 2,
    }
    assert {ex.name: ex.value for ex in operation.query["schema_all_of"].examples} == {
        "example_0": {
            "base": "nested",
        },
        "examples_1": {
            "extra": "addon",
        },
        "examples_2": {
            "more": "A",
        },
        "examples_3": {
            "more": "B",
        },
    }


def test_find_operation_by_id_or_label():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {"operationId": "getUsers", "responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"201": {"description": "Created"}}},  # No operationId
            },
            "/users/{id}": {"get": {"operationId": "getUserById", "responses": {"200": {"description": "OK"}}}},
        },
    }

    schema = OpenApi(raw_schema)

    assert schema.find_operation_by_id("getUsers").label == "GET /users"
    assert schema.find_operation_by_id("getUserById").label == "GET /users/{id}"
    assert schema.find_operation_by_id("nonExistent") is None
    assert schema.find_operation_by_label("GET /users").label == "GET /users"
    assert schema.find_operation_by_label("GET /users/{id}").label == "GET /users/{id}"
    assert schema.find_operation_by_label("nonExistent") is None
    assert schema.find_operation_by_label("GET /unknown") is None


def test_find_operation_by_id_swagger_2():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {"/users": {"get": {"operationId": "listUsers", "responses": {"200": {"description": "OK"}}}}},
    }

    schema = OpenApi(raw_schema)
    assert schema.find_operation_by_id("listUsers").label == "GET /users"


def test_find_operation_by_id_with_references():
    def retrieve(uri: str):
        if uri == "path-items.json":
            return Resource.opaque(
                {
                    "UsersPaths": {
                        "get": {"operationId": "getUsersFromRef", "responses": {"200": {"description": "OK"}}},
                        "post": {"$ref": "operations.json#/CreateUser"},
                    }
                }
            )
        else:
            raise NoSuchResource(ref=uri)

    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {"$ref": "path-items.json#/UsersPaths"},
        },
    }

    schema = OpenApi(raw_schema, retrieve=retrieve)

    assert schema.find_operation_by_id("getUsersFromRef").label == "GET /users"


def test_find_operation_by_id_broken_references():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {"$ref": "missing.json#/PathItem"},
        },
    }

    schema = OpenApi(raw_schema)

    assert schema.find_operation_by_id("brokenOp") is None


def test_find_operation_by_reference():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {"operationId": "getUsers", "responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"201": {"description": "Created"}}},
            },
            "/users/{user_id}": {
                "patch": {"operationId": "updateUser", "responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"204": {"description": "Deleted"}}},
            },
            "/special/~path": {"get": {"responses": {"200": {"description": "OK"}}}},
        },
    }

    OPENAPI_30_VALIDATOR.validate(raw_schema)
    schema = OpenApi(raw_schema)

    assert schema.find_operation_by_ref("#/paths/~1users/get").label == "GET /users"
    assert schema.find_operation_by_ref("#/paths/~1users/post").label == "POST /users"
    assert schema.find_operation_by_ref("#/paths/~1users~1{user_id}/patch").label == "PATCH /users/{user_id}"
    assert schema.find_operation_by_ref("#/paths/~1users~1{user_id}/delete").label == "DELETE /users/{user_id}"
    assert schema.find_operation_by_ref("#/paths/~1special~1~0path/get").label == "GET /special/~path"
    assert schema.find_operation_by_ref("#/paths/~1users/put") is None
    assert schema.find_operation_by_ref("#/paths/~1nonexistent/get") is None
    assert schema.find_operation_by_ref("#/paths/~1users") is None
    assert schema.find_operation_by_ref("#/info/title") is None
    assert schema.find_operation_by_ref("invalid-reference") is None


def test_response_links_openapi_3():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "components": {
            "links": {"RefLink": {"operationId": "getUserById", "parameters": {"userId": "$response.body#/id"}}}
        },
        "paths": {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "User created",
                            "links": {
                                "GetUser": {
                                    "operationId": "getUserById",
                                    "parameters": {"userId": "$response.body#/id"},
                                    "description": "Get the created user",
                                },
                                "GetUserRepos": {
                                    "operationRef": "#/paths/~1users~1{userId}~1repos/get",
                                    "parameters": {"userId": "$response.body#/id"},
                                },
                                "ReferencedLink": {"$ref": "#/components/links/RefLink"},
                            },
                        },
                        "400": {
                            "description": "Bad request"
                            # No links
                        },
                    }
                }
            }
        },
    }

    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    operation = schema["/users"]["POST"]

    assert (
        operation.responses["201"].links["GetUser"].definition
        == raw_schema["paths"]["/users"]["post"]["responses"]["201"]["links"]["GetUser"]
    )

    assert set(operation.responses["201"].links) == {"GetUser", "GetUserRepos", "ReferencedLink"}

    assert len(operation.responses["400"].links) == 0
    assert list(operation.responses["400"].links) == []


def test_response_links_swagger_2():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "User created",
                            "x-links": {
                                "GetCreatedUser": {
                                    "operationId": "getUserById",
                                    "parameters": {"id": "$response.body#/userId"},
                                },
                                "ListUsers": {"operationId": "listUsers"},
                            },
                        }
                    }
                }
            }
        },
    }

    SWAGGER_20_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema)
    response = schema["/users"]["POST"].responses["201"]

    assert (
        response.links["GetCreatedUser"].definition
        == raw_schema["paths"]["/users"]["post"]["responses"]["201"]["x-links"]["GetCreatedUser"]
    )
    assert (
        response.links["ListUsers"].definition
        == raw_schema["paths"]["/users"]["post"]["responses"]["201"]["x-links"]["ListUsers"]
    )
    assert set(response.links) == {"GetCreatedUser", "ListUsers"}


def test_response_links_references():
    def retrieve(uri: str):
        if uri == "links.json":
            return Resource.opaque(
                {
                    "UserLink": {
                        "operationRef": "#/paths/~1users~1{id}/get",
                        "parameters": {"id": "$response.body#/userId"},
                    }
                }
            )
        else:
            raise NoSuchResource(ref=uri)

    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "post": {
                    "responses": {
                        "201": {"description": "Created", "links": {"ExternalRef": {"$ref": "links.json#/UserLink"}}}
                    }
                }
            }
        },
    }
    OPENAPI_30_VALIDATOR.validate(raw_schema)

    schema = OpenApi(raw_schema, retrieve=retrieve)
    response = schema["/users"]["POST"].responses["201"]

    assert response.links["ExternalRef"].definition["operationRef"] == "#/paths/~1users~1{id}/get"
    assert len(response.links) == 1


def test_is_deprecated():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {"deprecated": True, "responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"201": {"description": "Created"}}},  # No operationId
            },
        },
    }

    OPENAPI_30_VALIDATOR.validate(raw_schema)
    schema = OpenApi(raw_schema)

    assert schema.find_operation_by_label("GET /users").is_deprecated
    assert not schema.find_operation_by_label("POST /users").is_deprecated
