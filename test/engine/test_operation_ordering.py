import threading
from queue import Queue

import pytest

import schemathesis
from schemathesis.engine.phases.unit._layered_scheduler import LayeredScheduler
from schemathesis.engine.phases.unit._ordering import compute_operation_layers
from schemathesis.specs.openapi.stateful.dependencies.layers import compute_dependency_layers


def test_restful_heuristic_ordering(ctx):
    schema_dict = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {"responses": {"201": {"description": "Created"}}},
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/users/{id}": {
                "get": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "patch": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
        }
    )
    loaded = schemathesis.openapi.from_dict(schema_dict)

    operations = list(loaded.get_all_operations())
    ops = [op.ok() for op in operations]

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


def test_layered_scheduler_single_layer(ctx):
    schema_dict = ctx.openapi.build_schema(
        {
            "/health": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/status": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    loaded = schemathesis.openapi.from_dict(schema_dict)

    operations = list(loaded.get_all_operations())
    ops = [op.ok() for op in operations]

    scheduler = LayeredScheduler([ops])

    # Should be able to get both operations
    result1 = scheduler.next_operation()
    assert result1 is not None

    result2 = scheduler.next_operation()
    assert result2 is not None

    # Layer exhausted - with single worker, returns None immediately
    result3 = scheduler.next_operation()
    assert result3 is None


def test_layered_scheduler_multiple_layers(ctx):
    schema_dict = ctx.openapi.build_schema(
        {
            "/users": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/users/{id}": {
                "get": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    loaded = schemathesis.openapi.from_dict(schema_dict)

    operations = list(loaded.get_all_operations())
    ops = [op.ok() for op in operations]

    # Manually create layers: POST first, then GET
    post_ops = [op for op in ops if op.method.upper() == "POST"]
    get_ops = [op for op in ops if op.method.upper() == "GET"]

    scheduler = LayeredScheduler([post_ops, get_ops])

    # Get POST operation from layer 0
    result = scheduler.next_operation()
    assert result is not None
    assert result.ok().method.upper() == "POST"

    # Layer 0 exhausted, automatically advances to layer 1
    result = scheduler.next_operation()
    assert result is not None
    assert result.ok().method.upper() == "GET"

    # All layers exhausted
    result = scheduler.next_operation()
    assert result is None


def test_layered_scheduler_multi_worker_coordination(ctx):
    schema_dict = ctx.openapi.build_schema(
        {
            "/users/{id}": {
                "get": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/products/{id}": {
                "get": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/users": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/products": {"post": {"responses": {"201": {"description": "Created"}}}},
        }
    )
    loaded = schemathesis.openapi.from_dict(schema_dict)

    operations = list(loaded.get_all_operations())
    ops = [op.ok() for op in operations]

    post_ops = [op for op in ops if op.method.upper() == "POST"]
    get_ops = [op for op in ops if op.method.upper() == "GET"]
    delete_ops = [op for op in ops if op.method.upper() == "DELETE"]

    scheduler = LayeredScheduler([post_ops, get_ops, delete_ops])

    results_queue = Queue()

    def worker():
        while True:
            result = scheduler.next_operation()
            if result is None:
                break
            op = result.ok()
            results_queue.put((threading.get_ident(), op.method.upper(), op.path))

    threads = []
    workers_num = 3
    for _ in range(workers_num):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=5.0)

    all_results = []
    while not results_queue.empty():
        all_results.append(results_queue.get())

    assert len(all_results) == len(post_ops) + len(get_ops) + len(delete_ops)

    post_indices = []
    get_indices = []
    delete_indices = []
    for idx, (_, method, _) in enumerate(all_results):
        if method == "POST":
            post_indices.append(idx)
        elif method == "GET":
            get_indices.append(idx)
        elif method == "DELETE":
            delete_indices.append(idx)

    if post_indices and get_indices:
        max_post_idx = max(post_indices)
        min_get_idx = min(get_indices)
        assert max_post_idx < min_get_idx

    if get_indices and delete_indices:
        max_get_idx = max(get_indices)
        min_delete_idx = min(delete_indices)
        assert max_get_idx < min_delete_idx


def test_dependency_layers_with_links(ctx):
    schema = ctx.openapi.build_schema(
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
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    loaded = schemathesis.openapi.from_dict(schema)

    # Dependency graph should show POST -> GET relationship
    graph = loaded.analysis.dependency_graph
    layers = compute_dependency_layers(graph)

    if layers:
        # POST should be in earlier layer than GET
        post_layer = None
        get_layer = None

        for idx, layer in enumerate(layers):
            for label in layer:
                if "POST" in label and "/users/{id}" not in label:
                    post_layer = idx
                elif "GET" in label and "/users/{id}" in label:
                    get_layer = idx

        if post_layer is not None and get_layer is not None:
            assert post_layer < get_layer


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
        op["parameters"] = [{"in": "path", "name": param, "required": True, "schema": {"type": "string"}}]
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
            [{"GET /users"}, {"GET /users/{id}", "PUT /users/{id}"}],
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
    schema = ctx.openapi.build_schema(operations)
    loaded = schemathesis.openapi.from_dict(schema)

    graph = loaded.analysis.dependency_graph
    layers = compute_dependency_layers(graph)

    assert layers is not None
    assert len(layers) == len(expected_layers)
    for i, expected_layer in enumerate(expected_layers):
        assert set(layers[i]) == expected_layer
