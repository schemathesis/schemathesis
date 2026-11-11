import pytest

from schemathesis.specs.openapi._ordering import compute_operation_layers
from schemathesis.specs.openapi.stateful.dependencies.layers import compute_dependency_layers


def _operations(schema):
    return [operation.ok() for operation in schema.get_all_operations()]


def _path_param(name="id", param_type="integer"):
    return {"name": name, "in": "path", "required": True, "schema": {"type": param_type}}


def test_restful_heuristic_ordering(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {"responses": {"201": {"description": "Created"}}},
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/users/{id}": {
                "get": {
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                },
                "patch": {
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [_path_param()],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
        }
    )

    ops = _operations(loaded)

    layers = compute_operation_layers(loaded, ops)

    # Layer 0: POST (creates resources)
    # Layer 1: GET, PATCH (reads/updates)
    # Layer 2: DELETE (cleanup)
    assert len(layers) >= 2

    layer_0_methods = {op.method.upper() for op in layers[0]}
    assert "POST" in layer_0_methods

    if len(layers) > 1:
        layer_1_methods = {op.method.upper() for op in layers[1]}
        assert "GET" in layer_1_methods or "PATCH" in layer_1_methods

    if len(layers) > 2:
        layer_2_methods = {op.method.upper() for op in layers[2]}
        assert "DELETE" in layer_2_methods


def test_dependency_layers_restful_order_within_layer(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"userId": {"type": "string"}}}
                                }
                            },
                        }
                    }
                },
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"userId": {"type": "string"}}}
                                }
                            },
                        }
                    }
                },
            },
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"itemId": {"type": "string"}}}
                                }
                            },
                        }
                    }
                },
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"itemId": {"type": "string"}}}
                                }
                            },
                        }
                    }
                },
            },
            "/users/{userId}": {
                "get": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/items/{itemId}": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    layers = compute_dependency_layers(loaded.analysis.dependency_graph)

    # Layer 0: Collection operations (produce resources) - POST before GET
    # Layer 1: Item operations (depend on collection outputs) - GET before DELETE
    assert layers == [
        ["POST /items", "POST /users", "GET /items", "GET /users"],
        ["GET /items/{itemId}", "GET /users/{userId}", "DELETE /users/{userId}"],
    ]


def test_dependency_layers_with_links(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"id": "$response.body#/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    assert compute_dependency_layers(loaded.analysis.dependency_graph) == [["POST /users"], ["GET /users/{id}"]]


def _make_operation(op_id, path, method="get", param="id", links=None, request_body_schema=None):
    op = {
        "operationId": op_id,
        "responses": {
            "200" if method == "get" else "201": {
                "description": "OK" if method == "get" else "Created",
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"id": {"type": "string"}}}}
                },
            }
        },
    }
    if "{" in path:
        op["parameters"] = [{"name": param, "in": "path", "required": True, "schema": {"type": "string"}}]
    if request_body_schema:
        op["requestBody"] = {"content": {"application/json": {"schema": request_body_schema}}}
    if links:
        op["responses"]["200" if method == "get" else "201"]["links"] = links
    return {path: {method: op}}


def _merge_operations(*ops):
    result = {}
    for op in ops:
        for path, methods in op.items():
            if path not in result:
                result[path] = {}
            result[path].update(methods)
    return result


@pytest.mark.parametrize(
    ["operations", "expected_layers"],
    [
        pytest.param(
            {
                "/sandbox": {
                    "post": {
                        "operationId": "createSandbox",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"sandboxId": {"type": "string"}}}
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"sandboxId": {"type": "string"}}}
                                    }
                                },
                            }
                        },
                    }
                },
                "/sandbox/{sandboxId}": {
                    "get": {
                        "operationId": "getSandbox",
                        "parameters": [
                            {"name": "sandboxId", "in": "path", "required": True, "schema": {"type": "string"}}
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"sandboxId": {"type": "string"}}}
                                    }
                                },
                            }
                        },
                    }
                },
            },
            [{"POST /sandbox", "GET /sandbox/{sandboxId}"}],
            id="simple-cycle-inferred-dependencies",
        ),
        pytest.param(
            {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "parameters": [{"name": "subscriptionId", "in": "query", "schema": {"type": "string"}}],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "subscriptionId": {"type": "string"},
                                                "users": {"type": "array", "items": {"type": "object"}},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/users/{id}": {
                    "get": {
                        "operationId": "getUser",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "subscriptionId", "in": "query", "schema": {"type": "string"}},
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"subscriptionId": {"type": "string"}},
                                        }
                                    }
                                },
                            }
                        },
                    },
                    "put": {
                        "operationId": "updateUser",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "subscriptionId", "in": "query", "schema": {"type": "string"}},
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"subscriptionId": {"type": "string"}},
                                        }
                                    }
                                },
                            }
                        },
                    },
                },
            },
            [{"GET /users", "GET /users/{id}", "PUT /users/{id}"}],
            id="multi-operation-cycle",
        ),
        pytest.param(
            {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "parameters": [{"name": "itemId", "in": "query", "schema": {"type": "string"}}],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"itemId": {"type": "string"}},
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/items/{id}": {
                    "get": {
                        "operationId": "getItem",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "itemId", "in": "query", "schema": {"type": "string"}},
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"itemId": {"type": "string"}}}
                                    }
                                },
                            }
                        },
                    },
                    "put": {
                        "operationId": "updateItem",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "itemId", "in": "query", "schema": {"type": "string"}},
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"itemId": {"type": "string"}}}
                                    }
                                },
                            }
                        },
                    },
                    "delete": {
                        "operationId": "deleteItem",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "itemId", "in": "query", "schema": {"type": "string"}},
                        ],
                        "responses": {"204": {"description": "Deleted"}},
                    },
                },
            },
            [{"GET /items", "GET /items/{id}", "PUT /items/{id}"}, {"DELETE /items/{id}"}],
            id="cycle-with-dependent-operations",
        ),
    ],
)
def test_cycle_detection_inferred_dependencies(ctx, operations, expected_layers):
    loaded = ctx.openapi.load_schema(operations)

    graph = loaded.analysis.dependency_graph
    layers = compute_dependency_layers(graph)

    assert layers is not None
    assert len(layers) == len(expected_layers)
    for i, expected_layer in enumerate(expected_layers):
        assert set(layers[i]) == expected_layer


def test_dependent_of_two_cycles_waits_for_both(ctx):
    items_response = {
        "200": {
            "description": "OK",
            "content": {
                "application/json": {"schema": {"type": "object", "properties": {"itemId": {"type": "string"}}}}
            },
        }
    }
    users_response = {
        "200": {
            "description": "OK",
            "content": {
                "application/json": {"schema": {"type": "object", "properties": {"userId": {"type": "string"}}}}
            },
        }
    }
    item_query = {"name": "itemId", "in": "query", "schema": {"type": "string"}}
    user_query = {"name": "userId", "in": "query", "schema": {"type": "string"}}
    id_path = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
    loaded = ctx.openapi.load_schema(
        {
            "/items": {"get": {"parameters": [item_query], "responses": items_response}},
            "/items/{id}": {"get": {"parameters": [id_path, item_query], "responses": items_response}},
            "/users": {"get": {"parameters": [user_query], "responses": users_response}},
            "/users/{id}": {"get": {"parameters": [id_path, user_query], "responses": users_response}},
            "/report": {"get": {"parameters": [item_query, user_query], "responses": {"204": {"description": "OK"}}}},
        }
    )

    assert compute_dependency_layers(loaded.analysis.dependency_graph) == [
        ["GET /items", "GET /items/{id}", "GET /users", "GET /users/{id}"],
        ["GET /report"],
    ]
