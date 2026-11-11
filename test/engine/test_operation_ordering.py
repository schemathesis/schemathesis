import threading
from queue import Queue

from flask import jsonify

import schemathesis
from schemathesis.engine import events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName
from schemathesis.engine.run.unit import LayeredScheduler, _create_scheduler
from test.apps.fragments.schemas import null_name_responses
from test.utils import EventStream


def _operations(schema):
    return [operation.ok() for operation in schema.get_all_operations()]


def _ops_by_method(operations, method):
    method = method.upper()
    return [operation for operation in operations if operation.method.upper() == method]


def _path_param(name="id", param_type="integer"):
    return {"name": name, "in": "path", "required": True, "schema": {"type": param_type}}


def test_layered_scheduler_single_layer(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/health": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/status": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    ops = _operations(loaded)

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
    loaded = ctx.openapi.load_schema(
        {
            "/users": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/users/{id}": {
                "get": {
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    ops = _operations(loaded)

    # Manually create layers: POST first, then GET
    post_ops = _ops_by_method(ops, "POST")
    get_ops = _ops_by_method(ops, "GET")

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


def test_layered_scheduler_holds_consumers_until_producers_finish(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/users": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/users/{id}": {
                "get": {
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    operations = _operations(loaded)
    scheduler = LayeredScheduler([_ops_by_method(operations, "POST"), _ops_by_method(operations, "GET")])
    producer_started = threading.Event()
    allow_producer_to_finish = threading.Event()
    consumer_waiting = threading.Event()
    consumer_dispatched = threading.Event()

    def run_producer():
        assert scheduler.next_operation().ok().method.upper() == "POST"
        producer_started.set()
        assert allow_producer_to_finish.wait(timeout=5)
        scheduler.release()

    def run_consumer():
        assert producer_started.wait(timeout=5)
        consumer_waiting.set()
        assert scheduler.next_operation().ok().method.upper() == "GET"
        consumer_dispatched.set()

    producer = threading.Thread(target=run_producer)
    consumer = threading.Thread(target=run_consumer)
    producer.start()
    consumer.start()

    # The next layer must not start while the POST is still running
    assert consumer_waiting.wait(timeout=5)
    assert not consumer_dispatched.wait(timeout=0.2)

    allow_producer_to_finish.set()
    assert consumer_dispatched.wait(timeout=5)
    producer.join()
    consumer.join()


def test_layered_scheduler_multi_worker_coordination(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/users/{id}": {
                "get": {
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [_path_param()],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/products/{id}": {
                "get": {
                    "parameters": [_path_param()],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [_path_param()],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/users": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/products": {"post": {"responses": {"201": {"description": "Created"}}}},
        }
    )

    ops = _operations(loaded)

    post_ops = _ops_by_method(ops, "POST")
    get_ops = _ops_by_method(ops, "GET")
    delete_ops = _ops_by_method(ops, "DELETE")

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

    methods = [method for _, method, _ in all_results]
    paths = [path for _, _, path in all_results]

    assert sorted(methods) == sorted(["POST"] * len(post_ops) + ["GET"] * len(get_ops) + ["DELETE"] * len(delete_ops))

    expected_paths = sorted([op.path for op in post_ops + get_ops + delete_ops])
    assert sorted(paths) == expected_paths


def test_create_scheduler_respects_layer_order_for_single_layer(ctx):
    # Methods listed GET-first to demonstrate that schema-iteration order does
    # not happen to match the desired RESTful order. The scheduler should
    # follow the layer's POST-first sort regardless of dict order.
    loaded = ctx.openapi.load_schema(
        {
            "/products/{productName}": {
                "get": {
                    "operationId": "getProduct",
                    "parameters": [
                        {"name": "productName", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": null_name_responses(),
                },
                "post": {
                    "operationId": "createProduct",
                    "parameters": [
                        {"name": "productName", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"201": {"description": "Created"}},
                },
            }
        }
    )

    engine = EngineContext(schema=loaded, stop_event=threading.Event())
    phase = Phase(name=PhaseName.FUZZING, is_enabled=True)

    scheduler, _ = _create_scheduler(engine, phase)

    dispatched: list[str] = []
    while True:
        result = scheduler.next_operation()
        if result is None:
            break
        dispatched.append(result.ok().label)
    assert dispatched == ["POST /products/{productName}", "GET /products/{productName}"]


# An operation whose response `$ref` cannot be resolved drops out of the dependency graph, but must still be tested.
def test_operation_with_unresolvable_ref_is_still_dispatched(ctx):
    loaded = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"userId": {"type": "string"}},
                                        "required": ["userId"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/users/{userId}": {
                "get": {
                    "parameters": [_path_param(name="userId", param_type="string")],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/processes": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"$ref": "./nonexistent.yaml#/Foo"}}},
                        }
                    }
                }
            },
        }
    )

    engine = EngineContext(schema=loaded, stop_event=threading.Event())
    scheduler, _ = _create_scheduler(engine, Phase(name=PhaseName.FUZZING, is_enabled=True))

    dispatched = []
    while True:
        result = scheduler.next_operation()
        if result is None:
            break
        dispatched.append(result.ok().label)

    assert sorted(dispatched) == sorted(operation.label for operation in _operations(loaded))


# A definition-level schema error must not abort a dependency-ordered run; it is reported after all layers finish.
def test_layered_run_reports_operations_with_schema_errors(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"userId": {"type": "string"}},
                                        "required": ["userId"],
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/users/{userId}": {
                "get": {
                    "parameters": [_path_param(name="userId", param_type="string")],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/broken": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Missing"}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/users", methods=["POST"])
    def create_user():
        return jsonify({"userId": "1"}), 201

    @app.route("/users/<user_id>")
    def get_user(user_id):
        return jsonify({})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    assert [(event.label, event.info.format()) for event in stream.find_all(events.NonFatalError)] == [
        (
            "GET /broken",
            "Schema Error\n\nUnresolvable reference in the schema\n\nError details:\n"
            "    Reference: #/components/parameters/Missing\n    Component does not exist in the schema.",
        )
    ]


def test_create_scheduler_filters_to_requested_operations(ctx):
    # Dependency layers are built from the whole graph, so a filtered run must not trip over the rest.
    loaded = ctx.openapi.load_schema(
        {
            "/products/{productName}": {
                "get": {
                    "operationId": "getProduct",
                    "parameters": [
                        {"name": "productName", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": null_name_responses(),
                },
                "post": {
                    "operationId": "createProduct",
                    "parameters": [
                        {"name": "productName", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"201": {"description": "Created"}},
                },
            }
        }
    )
    engine = EngineContext(schema=loaded, stop_event=threading.Event())
    phase = Phase(name=PhaseName.COVERAGE, is_enabled=True)

    scheduler, _ = _create_scheduler(engine, phase, only=frozenset({"GET /products/{productName}"}))

    dispatched: list[str] = []
    while True:
        result = scheduler.next_operation()
        if result is None:
            break
        dispatched.append(result.ok().label)
    assert dispatched == ["GET /products/{productName}"]
