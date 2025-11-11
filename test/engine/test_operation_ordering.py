import threading
import time

import schemathesis
from schemathesis.config import OperationOrdering
from schemathesis.core.result import Err, Ok, Result
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

    layers = compute_operation_layers(loaded, ops, OperationOrdering.AUTO)

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

    scheduler = LayeredScheduler([ops], workers_num=1)

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

    scheduler = LayeredScheduler([post_ops, get_ops], workers_num=1)

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


def test_layered_scheduler_waits_for_all_workers(ctx):
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

    post_ops = [op for op in ops if op.method.upper() == "POST"]
    get_ops = [op for op in ops if op.method.upper() == "GET"]

    wait_timeout = 0.05
    scheduler = LayeredScheduler([post_ops, get_ops], workers_num=3, wait_timeout=wait_timeout)

    allow_next_layer = threading.Event()
    post_started = threading.Event()
    get_assigned = threading.Event()
    errors: list[Exception] = []

    def track_get(result: Result | None) -> None:
        if isinstance(result, Ok) and result.ok().method.upper() == "GET":
            get_assigned.set()

    def guard(fn):
        def wrapper() -> None:
            try:
                fn()
            except Exception as exc:  # pragma: no cover - captured for assertions
                errors.append(exc)

        return wrapper

    def post_worker() -> None:
        try:
            result = scheduler.next_operation()
            assert isinstance(result, Ok)
            assert result.ok().method.upper() == "POST"
            post_started.set()
            assert allow_next_layer.wait(timeout=2.0)
            while True:
                result = scheduler.next_operation()
                track_get(result)
                if result is None:
                    break
        finally:
            scheduler.worker_stopped()

    def waiting_worker() -> None:
        try:
            assert post_started.wait(timeout=2.0)
            while True:
                result = scheduler.next_operation()
                track_get(result)
                if result is None:
                    break
        finally:
            scheduler.worker_stopped()

    threads = [
        threading.Thread(target=guard(post_worker)),
        threading.Thread(target=guard(waiting_worker)),
        threading.Thread(target=guard(waiting_worker)),
    ]

    for thread in threads:
        thread.start()

    assert post_started.wait(timeout=1.0)
    try:
        assert not get_assigned.wait(timeout=wait_timeout * 4)
    finally:
        allow_next_layer.set()

    assert get_assigned.wait(timeout=1.0)

    for thread in threads:
        thread.join(timeout=1.0)
        assert not thread.is_alive()

    assert not errors


def test_layered_scheduler_releases_waiters_when_worker_exits(ctx):
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

    post_ops = [op for op in ops if op.method.upper() == "POST"]
    get_ops = [op for op in ops if op.method.upper() == "GET"]

    wait_timeout = 0.05
    scheduler = LayeredScheduler([post_ops, get_ops], workers_num=2, wait_timeout=wait_timeout)

    waiting_started = threading.Event()
    get_assigned = threading.Event()
    errors: list[Exception] = []

    def waiting_worker() -> None:
        try:
            waiting_started.set()
            result = scheduler.next_operation()
            assert isinstance(result, Ok)
            assert result.ok().method.upper() == "GET"
            get_assigned.set()
        except Exception as exc:  # pragma: no cover - captured for assertions
            errors.append(exc)
        finally:
            scheduler.worker_stopped()

    # Main thread simulates the worker that owns the POST operation and exits early
    result = scheduler.next_operation()
    assert isinstance(result, Ok)
    assert result.ok().method.upper() == "POST"

    thread = threading.Thread(target=waiting_worker)
    thread.start()

    assert waiting_started.wait(timeout=1.0)
    time.sleep(wait_timeout * 4)

    # Worker that handled the POST exits without requesting more
    scheduler.worker_stopped()

    assert get_assigned.wait(timeout=1.0)

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not errors


def test_layered_scheduler_emits_errors(ctx):
    class DummyOperationError(Exception):
        def __init__(self, method: str, path: str) -> None:
            super().__init__(f"{method} {path}")
            self.method = method
            self.path = path

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

    post_ops = [op for op in ops if op.method.upper() == "POST"]
    get_ops = [op for op in ops if op.method.upper() == "GET"]

    error = Err(DummyOperationError("get", "/users"))

    scheduler = LayeredScheduler([post_ops, get_ops], workers_num=1, error_results=[error])

    first = scheduler.next_operation()
    assert isinstance(first, Err)
    assert isinstance(first.err(), DummyOperationError)

    post_result = scheduler.next_operation()
    assert isinstance(post_result, Ok)
    assert post_result.ok().method.upper() == "POST"

    get_result = scheduler.next_operation()
    assert isinstance(get_result, Ok)
    assert get_result.ok().method.upper() == "GET"


def test_operation_ordering_none_creates_single_layer(ctx):
    schema = ctx.openapi.build_schema(
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
    loaded = schemathesis.openapi.from_dict(schema)

    operations = list(loaded.get_all_operations())
    ops = [op.ok() for op in operations]

    layers = compute_operation_layers(loaded, ops, OperationOrdering.NONE)

    # With NONE strategy, all operations in single layer
    assert len(layers) == 1
    assert len(layers[0]) == len(ops)


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
