import uuid

import pytest
from flask import abort, jsonify, request


def _object_schema(properties, *, required=None, **extra):
    schema = {"type": "object", "properties": properties, **extra}
    if required is not None:
        schema["required"] = list(required)
    return schema


def _json_content(schema, **extra):
    return {"application/json": {"schema": schema, **extra}}


def _json_response(schema=None, *, description="OK", **extra):
    response = {}
    if description is not None:
        response["description"] = description
    if schema is not None:
        response["content"] = _json_content(schema)
    response.update(extra)
    return response


def _json_request_body(schema, *, required=True, **extra):
    return {"content": _json_content(schema, **extra), "required": required}


def _path_param(name, schema=None, **extra):
    return {"name": name, "in": "path", "required": True, "schema": schema or {"type": "string"}, **extra}


def _uuid_path_param(name, **extra):
    return _path_param(name, {"type": "string", "format": "uuid"}, **extra)


def _response_pool_config(*, enabled=True, operation_ordering=None):
    fuzzing = {"extra-data-sources": {"responses": enabled}}
    if operation_ordering is not None:
        fuzzing["operation-ordering"] = operation_ordering
    return {"phases": {"fuzzing": fuzzing}}


def _examples_fill_missing_config():
    return {"phases": {"examples": {"fill-missing": True}}}


def _examples_response_pool_config(*, enabled):
    return {"phases": {"examples": {"extra-data-sources": {"responses": enabled}}}}


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_records_from_mixed_success_failure_scenarios(cli, snapshot_cli, ctx):
    item_schema = _object_schema({"id": {"type": "string"}, "name": {"type": "string"}}, required=["id", "name"])
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": _json_request_body(_object_schema({"name": {"type": "string"}}, required=["name"])),
                    "responses": {
                        "201": _json_response(
                            item_schema,
                            description="Created",
                            links={
                                "GetItemById": {
                                    "operationId": "getItem",
                                    "parameters": {"id": "$response.body#/id"},
                                }
                            },
                        )
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [_path_param("id")],
                    "responses": {
                        "200": _json_response(item_schema, description="Success"),
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )
    items = {}
    post_count = [0]

    @app.route("/items", methods=["POST"])
    def create_item():
        post_count[0] += 1
        data = request.get_json() or {}
        # First few requests succeed, then fail with 500
        if post_count[0] <= 3:
            item_id = uuid.uuid4().hex
            item = {"id": item_id, "name": data.get("name", "Item")}
            items[item_id] = item
            return jsonify(item), 201
        else:
            abort(500)

    @app.route("/items/<item_id>", methods=["GET"])
    def get_item(item_id):
        if item_id not in items:
            return "", 404
        # Bug: missing required 'name' field - only discoverable with valid ID
        return jsonify({"id": items[item_id]["id"]}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=50",
            "-c not_a_server_error",
            "-c response_schema_conformance",
            "--mode=positive",
            config=_response_pool_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("config_enabled", [False, True])
def test_extra_data_sources_enables_bug_discovery(cli, snapshot_cli, ctx, config_enabled):
    item_schema = _object_schema({"id": {"type": "string"}, "name": {"type": "string"}}, required=["id", "name"])
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": _json_request_body(_object_schema({"name": {"type": "string"}}, required=["name"])),
                    "responses": {
                        "201": _json_response(
                            item_schema,
                            description="Created",
                            links={
                                "GetItemById": {
                                    "operationId": "getItem",
                                    "parameters": {"id": "$response.body#/id"},
                                }
                            },
                        )
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [_path_param("id")],
                    "responses": {
                        "200": _json_response(item_schema, description="Success"),
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )
    items = {}

    @app.route("/items", methods=["POST"])
    def create_item():
        data = request.get_json() or {}
        item_id = uuid.uuid4().hex
        item = {"id": item_id, "name": data.get("name", "Item")}
        items[item_id] = item
        return jsonify(item), 201

    @app.route("/items/<item_id>", methods=["GET"])
    def get_item(item_id):
        if item_id not in items:
            return "", 404
        # Bug: response violates schema - missing required 'name' field
        return jsonify({"id": items[item_id]["id"]}), 200

    config = (
        _response_pool_config() if config_enabled else _response_pool_config(enabled=False, operation_ordering="none")
    )

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "-c response_schema_conformance",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_with_body_parameters(cli, snapshot_cli, ctx):
    project_schema = _object_schema({"id": {"type": "string"}, "name": {"type": "string"}}, required=["id", "name"])
    task_request_schema = _object_schema(
        {"project_id": {"type": "string"}, "title": {"type": "string"}}, required=["project_id", "title"]
    )
    task_response_schema = _object_schema(
        {"id": {"type": "string"}, "project_id": {"type": "string"}, "title": {"type": "string"}},
        required=["id", "project_id", "title"],
    )
    app, _ = ctx.openapi.make_flask_app(
        {
            "/projects": {
                "post": {
                    "operationId": "createProject",
                    "requestBody": _json_request_body(_object_schema({"name": {"type": "string"}}, required=["name"])),
                    "responses": {
                        "201": _json_response(
                            project_schema,
                            description="Created",
                            links={
                                "AddTask": {
                                    "operationId": "createTask",
                                    "parameters": {"project_id": "$response.body#/id"},
                                }
                            },
                        )
                    },
                }
            },
            "/tasks": {
                "post": {
                    "operationId": "createTask",
                    "requestBody": _json_request_body(task_request_schema),
                    "responses": {
                        "201": _json_response(task_response_schema, description="Created"),
                        "404": {"description": "Project not found"},
                    },
                }
            },
        }
    )
    projects = {}
    tasks = {}

    @app.route("/projects", methods=["POST"])
    def create_project():
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400)
        project_id = uuid.uuid4().hex
        project = {"id": project_id, "name": data.get("name", "Project")}
        projects[project_id] = project
        return jsonify(project), 201

    @app.route("/tasks", methods=["POST"])
    def create_task():
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400)
        project_id = data.get("project_id")
        if not project_id or project_id not in projects:
            abort(404)
        task_id = uuid.uuid4().hex
        task = {"id": task_id, "project_id": project_id, "title": data.get("title", "Task")}
        tasks[task_id] = task
        # Bug: the response violates the schema - missing the required "title" field
        return jsonify({"id": task_id, "project_id": project_id}), 201

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "-c response_schema_conformance",
            "--mode=positive",
            config=_response_pool_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("extra_data_enabled", [True, False])
def test_extra_data_sources_with_response_examples_prepopulation(cli, snapshot_cli, ctx, extra_data_enabled):
    known_user_id = "seeded-user-abc-123"
    user_schema = _object_schema({"id": {"type": "string"}, "name": {"type": "string"}}, required=["id", "name"])
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": _json_request_body(_object_schema({"name": {"type": "string"}}, required=["name"])),
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": user_schema,
                                    # Response example with a known static ID that matches pre-seeded data
                                    "example": {"id": known_user_id, "name": "Seeded User"},
                                }
                            },
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"user_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [_path_param("user_id")],
                    "responses": {
                        "200": _json_response(user_schema, description="Success"),
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )
    # Simulating a pre-seeded database with a known user
    seeded_users = {known_user_id: {"id": known_user_id, "name": "Seeded User"}}

    @app.route("/users", methods=["POST"])
    def create_user():
        # POST always fails - the only valid user is the pre-seeded one
        abort(500)

    @app.route("/users/<user_id>", methods=["GET"])
    def get_user(user_id):
        if user_id not in seeded_users:
            abort(404)
        # Bug: response violates schema - missing required 'name' field
        return jsonify({"id": seeded_users[user_id]["id"]}), 200

    # With extra_data_enabled=True, response examples are pre-populated and
    # the bug in GET /users/{user_id} is discovered. With False, only 404s occur.
    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=50",
            "-c response_schema_conformance",
            "--mode=positive",
            config=_response_pool_config(enabled=extra_data_enabled, operation_ordering="none"),
        )
        == snapshot_cli
    )


_POST_ITEMS_SCHEMA = {
    "operationId": "createItem",
    "requestBody": _json_request_body(
        _object_schema({"name": {"type": "string"}}, required=["name"]),
        examples={"valid": {"value": {"name": "test-item"}}},
    ),
    "responses": {
        "201": _json_response(
            _object_schema({"id": {"type": "string"}, "name": {"type": "string"}}, required=["id", "name"]),
            description="Created",
            links={
                "GetItemById": {
                    "operationId": "getItem",
                    "parameters": {"id": "$response.body#/id"},
                }
            },
        )
    },
}


def _make_items_app(ctx, get_params):
    """GET /items/{id} triggers a 500 only when called with a real UUID from POST."""
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {"post": _POST_ITEMS_SCHEMA},
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": get_params,
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )
    items = {}

    @app.route("/items", methods=["POST"])
    def create_item():
        data = request.get_json() or {}
        item_id = uuid.uuid4().hex
        items[item_id] = {"id": item_id, "name": data.get("name", "item")}
        return jsonify(items[item_id]), 201

    @app.route("/items/<item_id>", methods=["GET"])
    def get_item(item_id):
        if item_id not in items:
            return "", 404
        abort(500)  # reachable only with a real UUID from POST

    return app


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_examples_phase_merges_pool_with_schema_examples(cli, snapshot_cli, ctx):
    # `filter` has a schema example (keeps the phase active), `id` has none.
    # Pool supplies the real `id` from POST, exposing the bug.
    get_params = [
        _path_param("id"),
        {"name": "filter", "in": "query", "schema": {"type": "string", "example": "active"}},
    ]

    assert (
        cli.run_openapi_app(
            _make_items_app(ctx, get_params),
            "--phases=examples",
            "-c not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_examples_phase_uses_pool_as_fill_missing_source(cli, snapshot_cli, ctx):
    # No schema examples - GET would be skipped without fill-missing.
    # With fill-missing=true, pool supplies the real `id` and exposes the bug.
    get_params = [_path_param("id")]

    assert (
        cli.run_openapi_app(
            _make_items_app(ctx, get_params),
            "--phases=examples",
            "-c not_a_server_error",
            "--mode=positive",
            config=_examples_fill_missing_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_with_examples_and_fuzzing_phases(cli, snapshot_cli, ctx):
    user_schema = _object_schema({"id": {"type": "string"}, "email": {"type": "string"}}, required=["id", "email"])
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": _json_request_body(
                        _object_schema({"email": {"type": "string"}}, required=["email"]),
                        examples={"valid": {"value": {"email": "test@example.com"}}},
                    ),
                    "responses": {
                        "201": _json_response(
                            user_schema,
                            description="Created",
                            links={
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"user_id": "$response.body#/id"},
                                }
                            },
                        )
                    },
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [_path_param("user_id")],
                    "responses": {
                        "200": _json_response(user_schema, description="Success"),
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )
    users = {}

    @app.route("/users", methods=["POST"])
    def create_user():
        data = request.get_json() or {}
        user_id = uuid.uuid4().hex
        user = {"id": user_id, "email": data.get("email", "user@example.com")}
        users[user_id] = user
        return jsonify(user), 201

    @app.route("/users/<user_id>", methods=["GET"])
    def get_user(user_id):
        if user_id not in users:
            abort(404)
        # Bug: response violates schema - missing the required "email" field
        return jsonify({"id": users[user_id]["id"]}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=examples,fuzzing",
            "--max-examples=50",
            "-c response_schema_conformance",
            "--mode=positive",
            config=_response_pool_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_examples_phase_uses_pool_for_body_fields(cli, snapshot_cli, ctx):
    # Without body-side pool consumption, fill-missing has nothing to fill the body-only consumer with.
    session_schema = _object_schema({"sessionId": {"type": "string", "format": "uuid"}}, required=["sessionId"])
    app, _ = ctx.openapi.make_flask_app(
        {
            "/sessions": {
                "post": {
                    "operationId": "createSession",
                    "requestBody": _json_request_body(
                        session_schema,
                        examples={"valid": {"value": {"sessionId": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"}}},
                    ),
                    "responses": {"201": _json_response(session_schema, description="Created")},
                }
            },
            "/events/log": {
                "post": {
                    "operationId": "logEvent",
                    "requestBody": _json_request_body(session_schema),
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )
    sessions: set[str] = set()

    @app.route("/sessions", methods=["POST"])
    def create_session():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return "", 400
        session_id = data.get("sessionId")
        if not isinstance(session_id, str):
            return "", 400
        sessions.add(session_id)
        return jsonify({"sessionId": session_id}), 201

    @app.route("/events/log", methods=["POST"])
    def log_event():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return "", 400
        session_id = data.get("sessionId")
        if session_id not in sessions:
            return "", 404
        abort(500)  # reachable only when body sessionId came from the pool

    assert (
        cli.run_openapi_app(
            app,
            "--phases=examples",
            "-c not_a_server_error",
            "--mode=positive",
            config=_examples_fill_missing_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_no_false_positive_when_pool_body_missing_required_fields(cli, snapshot_cli, ctx):
    # Pool-seeded body with only a subset of required fields must not trigger positive_data_acceptance.
    api = ctx.openapi.apps.sessions_and_log_event()

    assert (
        cli.run(
            api.schema_url,
            "--phases=examples",
            "-c positive_data_acceptance",
            "--mode=positive",
            config=_examples_fill_missing_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_pool_captures_ids_from_multi_array_root_get_list_response(cli, snapshot_cli, ctx):
    # Docker Engine /volumes shape: `{Volumes: [...], Warnings: [...]}`. Server-seeded names;
    # no POST creator. UUIDs make blind generation practically incapable of guessing.
    seeded_names = [
        "550e8400-e29b-41d4-a716-446655440000",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    ]
    bug_name = seeded_names[2]
    volume_schema = {
        "type": "object",
        "properties": {"Name": {"type": "string", "format": "uuid"}, "Driver": {"type": "string"}},
        "required": ["Name", "Driver"],
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/volumes": {
                "get": {
                    "operationId": "listVolumes",
                    "responses": {
                        "200": _json_response(
                            _object_schema(
                                {
                                    "Volumes": {"type": "array", "items": volume_schema},
                                    "Warnings": {"type": "array", "items": {"type": "string"}},
                                },
                                required=["Volumes"],
                            )
                        )
                    },
                }
            },
            "/volumes/{Name}": {
                "get": {
                    "operationId": "getVolume",
                    "parameters": [_uuid_path_param("Name")],
                    "responses": {
                        "200": _json_response(volume_schema),
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )

    @app.route("/volumes", methods=["GET"])
    def list_volumes():
        return jsonify({"Volumes": [{"Name": name, "Driver": "local"} for name in seeded_names], "Warnings": []})

    @app.route("/volumes/<name>", methods=["GET"])
    def get_volume(name):
        if name == bug_name:
            raise RuntimeError("planted bug")
        if name not in seeded_names:
            return "", 404
        return jsonify({"Name": name, "Driver": "local"})

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=30",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_parent_aware_pool_correlates_path_params(cli, snapshot_cli, ctx):
    # Deep path planted bug fires only when (productName, itemName) co-refer.
    # Without parent-aware draws, pool feeds independent values and the bug stays unreached.
    products: set[str] = set()
    items: set[tuple[str, str]] = set()
    spec = {
        "/products/{productName}": {
            "post": {
                "operationId": "createProduct",
                "parameters": [_uuid_path_param("productName")],
                "responses": {"201": {"description": "Created"}, "409": {"description": "Already exists"}},
            }
        },
        "/products/{productName}/items/{itemName}": {
            "post": {
                "operationId": "createItem",
                "parameters": [
                    _uuid_path_param("productName"),
                    _uuid_path_param("itemName"),
                ],
                "responses": {
                    "201": {"description": "Created"},
                    "404": {"description": "Product not found"},
                    "409": {"description": "Already exists"},
                },
            }
        },
        "/products/{productName}/items/{itemName}/sync": {
            "post": {
                "operationId": "syncItem",
                "parameters": [
                    _uuid_path_param("productName"),
                    _uuid_path_param("itemName"),
                ],
                "responses": {
                    "200": {"description": "Synced"},
                    "404": {"description": "Item not found in this product"},
                },
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(spec)

    @app.route("/products/<product_name>", methods=["POST"])
    def create_product(product_name):
        if product_name in products:
            return "", 409
        products.add(product_name)
        return "", 201

    @app.route("/products/<product_name>/items/<item_name>", methods=["POST"])
    def create_item(product_name, item_name):
        if product_name not in products:
            return "", 404
        if (product_name, item_name) in items:
            return "", 409
        items.add((product_name, item_name))
        return "", 201

    @app.route("/products/<product_name>/items/<item_name>/sync", methods=["POST"])
    def sync_item(product_name, item_name):
        if (product_name, item_name) not in items:
            return "", 404
        # Planted bug fires only when (productName, itemName) form a real parent-child pair.
        raise RuntimeError("planted bug")

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
        )
        == snapshot_cli
    )


def test_post_delete_pool_does_not_re_feed_deleted_ids(cli, ctx):
    # After a successful DELETE, the pool no longer feeds the deleted id to GET/PUT/PATCH
    # consumers. Verified by counting per-id calls server-side, not by snapshotting CLI output.
    items: dict[str, dict] = {}
    deleted_ids: set[str] = set()
    stale_get_calls = 0

    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": _json_response(
                            _object_schema({"id": {"type": "string", "format": "uuid"}}, required=["id"]),
                            description="Created",
                        )
                    },
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [_uuid_path_param("itemId")],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                },
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [_uuid_path_param("itemId")],
                    "responses": {"204": {"description": "Deleted"}, "404": {"description": "Not found"}},
                },
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create_item():
        item_id = uuid.uuid4().hex
        items[item_id] = {"id": item_id}
        return jsonify({"id": item_id}), 201

    @app.route("/items/<item_id>", methods=["GET"])
    def get_item(item_id):
        nonlocal stale_get_calls
        if item_id in deleted_ids:
            stale_get_calls += 1
            return "", 404
        if item_id not in items:
            return "", 404
        return jsonify(items[item_id]), 200

    @app.route("/items/<item_id>", methods=["DELETE"])
    def delete_item(item_id):
        if item_id not in items:
            return "", 404
        deleted_ids.add(item_id)
        del items[item_id]
        return "", 204

    cli.run_openapi_app(
        app,
        "--phases=fuzzing",
        "--mode=positive",
    )

    # Tombstone+eviction must keep stale-id GETs near zero
    assert stale_get_calls <= 5, f"pool kept feeding deleted ids: {stale_get_calls} stale GETs"


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("producer_shape", "wrap_response"),
    [
        ("top-level-array", lambda items: items),
        ("wrapped-array", lambda items: {"data": items}),
    ],
    ids=["top-level-array", "wrapped-array"],
)
def test_stateful_reaches_every_list_producer_element(cli, snapshot_cli, ctx, producer_shape, wrap_response):
    # The planted bug at the last seeded id is only reachable if the inferred link
    # samples across every element of the producer's list, not just the first one.
    seeded = [
        {"id": "w-1", "label": "alpha"},
        {"id": "w-2", "label": "beta"},
        {"id": "w-3", "label": "gamma"},
    ]
    bug_id = seeded[-1]["id"]
    widget_ref = "#/components/schemas/Widget"
    components = {
        "schemas": {
            "Widget": _object_schema({"id": {"type": "string"}, "label": {"type": "string"}}, required=["id", "label"])
        }
    }
    if producer_shape == "top-level-array":
        list_schema = {"type": "array", "items": {"$ref": widget_ref}}
    else:
        list_schema = {
            "type": "object",
            "properties": {"data": {"type": "array", "items": {"$ref": widget_ref}}},
            "required": ["data"],
        }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/widgets": {
                "get": {
                    "operationId": "listWidgets",
                    "responses": {"200": _json_response(list_schema)},
                }
            },
            "/widgets/{widgetId}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [_path_param("widgetId")],
                    "responses": {
                        "200": _json_response({"$ref": widget_ref}),
                        "404": {"description": "Not found"},
                    },
                }
            },
        },
        components=components,
    )

    @app.route("/widgets", methods=["GET"])
    def list_widgets():
        return jsonify(wrap_response(seeded))

    @app.route("/widgets/<widget_id>", methods=["GET"])
    def get_widget(widget_id):
        if widget_id == bug_id:
            raise RuntimeError("planted bug")
        for w in seeded:
            if w["id"] == widget_id:
                return jsonify(w)
        return "", 404

    assert (
        cli.run_openapi_app(
            app,
            "--phases=stateful",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("sub_cardinality", "sub_resource", "consumer_path", "consumer_op", "ids", "make_post"),
    [
        (
            "many",
            "Comment",
            "/comments/{commentId}",
            "getComment",
            ["c-1", "c-2", "c-3", "c-4"],
            lambda ids: [
                {"id": "p-1", "comments": [{"id": ids[0], "text": "x"}, {"id": ids[1], "text": "y"}]},
                {"id": "p-2", "comments": [{"id": ids[2], "text": "z"}, {"id": ids[3], "text": "w"}]},
            ],
        ),
        (
            "one",
            "Author",
            "/authors/{authorId}",
            "getAuthor",
            ["a-1", "a-2"],
            lambda ids: [
                {"id": "p-1", "author": {"id": ids[0], "name": "Alice"}},
                {"id": "p-2", "author": {"id": ids[1], "name": "Bob"}},
            ],
        ),
    ],
    ids=["many-children-per-parent", "one-child-per-parent"],
)
def test_pool_captures_subresources_from_every_parent(
    cli,
    snapshot_cli,
    ctx,
    sub_cardinality,
    sub_resource,
    consumer_path,
    consumer_op,
    ids,
    make_post,
):
    # Last child of the last parent is only reachable if every parent's sub-resources enter the pool.
    bug_id = ids[-1]
    sub_schema_ref = f"#/components/schemas/{sub_resource}"
    if sub_cardinality == "many":
        sub_property = {"type": "array", "items": {"$ref": sub_schema_ref}}
        sub_props = {"id": {"type": "string"}, "text": {"type": "string"}}
    else:
        sub_property = {"$ref": sub_schema_ref}
        sub_props = {"id": {"type": "string"}, "name": {"type": "string"}}
    sub_field = "comments" if sub_cardinality == "many" else "author"
    sub_param = "commentId" if sub_cardinality == "many" else "authorId"
    components = {
        "schemas": {
            sub_resource: _object_schema(sub_props, required=list(sub_props)),
            "Post": _object_schema({"id": {"type": "string"}, sub_field: sub_property}, required=["id", sub_field]),
        }
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/posts": {
                "get": {
                    "operationId": "listPosts",
                    "responses": {
                        "200": _json_response(
                            _object_schema(
                                {"data": {"type": "array", "items": {"$ref": "#/components/schemas/Post"}}},
                                required=["data"],
                            )
                        )
                    },
                }
            },
            consumer_path: {
                "get": {
                    "operationId": consumer_op,
                    "parameters": [_path_param(sub_param)],
                    "responses": {
                        "200": _json_response({"$ref": sub_schema_ref}),
                        "404": {"description": "Not found"},
                    },
                }
            },
        },
        components=components,
    )

    @app.route("/posts", methods=["GET"])
    def list_posts():
        return jsonify({"data": make_post(ids)})

    @app.route(
        consumer_path.replace("{commentId}", "<comment_id>").replace("{authorId}", "<author_id>"),
        methods=["GET"],
    )
    def get_sub(**kwargs):
        target = next(iter(kwargs.values()))
        if target == bug_id:
            raise RuntimeError("planted bug")
        if target not in ids:
            return "", 404
        return jsonify({"id": target, "text": "ok"} if sub_cardinality == "many" else {"id": target, "name": "ok"})

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=30",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_overlays_nested_body_foreign_key(cli, snapshot_cli, ctx):
    # Server returns 500 only when shipping.location_id matches a real Location id;
    # without the nested overlay random ints never hit a real id and the bug stays hidden.
    location_schema = _object_schema({"id": {"type": "integer"}}, required=["id"], example={"id": 11})
    app, _ = ctx.openapi.make_flask_app(
        {
            "/a/locations": {
                "post": {
                    "operationId": "createLocation",
                    "responses": {"201": _json_response(location_schema, description=None)},
                }
            },
            "/b/departments": {
                "post": {
                    "operationId": "createDepartment",
                    "requestBody": _json_request_body(
                        _object_schema(
                            {
                                "name": {"type": "string"},
                                "shipping": _object_schema(
                                    {"location_id": {"type": "integer"}, "note": {"type": "string"}}
                                ),
                            },
                            required=["shipping"],
                        )
                    ),
                    "responses": {
                        "201": {"description": "OK"},
                        "500": {"description": "Server error"},
                    },
                }
            },
        }
    )
    locations = [11, 22, 33]
    location_index = [0]

    @app.route("/a/locations", methods=["POST"])
    def create_location():
        loc_id = locations[location_index[0] % len(locations)]
        location_index[0] += 1
        return jsonify({"id": loc_id}), 201

    @app.route("/b/departments", methods=["POST"])
    def create_department():
        body = request.get_json(silent=True) or {}
        shipping = body.get("shipping") or {}
        if shipping.get("location_id") in locations:
            abort(500)
        return jsonify({"id": 1}), 201

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage,fuzzing",
            "--max-examples=30",
            "--mode=positive",
            "-c not_a_server_error",
            config=_response_pool_config(),
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_handles_boolean_body_schema(cli, snapshot_cli, ctx):
    # A `schema: true` body alongside pool-using operations must not derail generation.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "responses": {
                        "201": _json_response(
                            _object_schema({"id": {"type": "string"}}, required=["id"]),
                            description=None,
                        )
                    },
                }
            },
            "/widgets/{id}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [_path_param("id")],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/anything": {
                "post": {
                    "operationId": "postAnything",
                    "requestBody": _json_request_body(True),
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        version="3.1.0",
    )

    @app.route("/widgets", methods=["POST"])
    def create_widget():
        return jsonify({"id": "w-1"}), 201

    @app.route("/widgets/<widget_id>", methods=["GET"])
    def get_widget(widget_id):
        return jsonify({}), 200

    @app.route("/anything", methods=["POST"])
    def post_anything():
        return jsonify({"ok": True}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=5",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_examples_phase_disabled(cli, snapshot_cli, ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "create-item",
                    "requestBody": {
                        "content": _json_content(_object_schema({"name": {"type": "string"}}, required=["name"]))
                    },
                    "responses": {
                        "201": _json_response(
                            _object_schema({"id": {"type": "string", "format": "uuid"}}, required=["id"]),
                            description="Created",
                        )
                    },
                }
            },
            "/items/{item_id}": {
                "get": {
                    "operationId": "get-item",
                    "parameters": [_uuid_path_param("item_id", example="00000000-0000-0000-0000-000000000001")],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                },
                "delete": {
                    "operationId": "delete-item",
                    "parameters": [_uuid_path_param("item_id", example="00000000-0000-0000-0000-000000000002")],
                    "responses": {"204": {"description": "Deleted"}, "404": {"description": "Not found"}},
                },
            },
        }
    )

    assert (
        cli.run_openapi_app(
            app,
            "--phases=examples",
            config=_examples_response_pool_config(enabled=False),
        )
        == snapshot_cli
    )
