import uuid

import pytest
from flask import abort, jsonify, request


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_records_from_mixed_success_failure_scenarios(cli, app_runner, snapshot_cli, ctx):
    item_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": item_schema}},
                            "links": {
                                "GetItemById": {
                                    "operationId": "getItem",
                                    "parameters": {"id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Success", "content": {"application/json": {"schema": item_schema}}},
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

    port = app_runner.run_flask_app(app)

    config = {
        "phases": {
            "fuzzing": {"extra-data-sources": {"responses": True}},
        },
    }

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=50",
            "-c not_a_server_error",
            "-c response_schema_conformance",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("config_enabled", [False, True])
def test_extra_data_sources_enables_bug_discovery(cli, app_runner, snapshot_cli, ctx, config_enabled):
    item_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": item_schema}},
                            "links": {
                                "GetItemById": {
                                    "operationId": "getItem",
                                    "parameters": {"id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Success", "content": {"application/json": {"schema": item_schema}}},
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

    port = app_runner.run_flask_app(app)

    if config_enabled:
        config = {
            "phases": {
                "fuzzing": {"extra-data-sources": {"responses": True}},
            },
        }
    else:
        config = {
            "phases": {
                "fuzzing": {
                    "operation-ordering": "none",
                    "extra-data-sources": {"responses": False},
                },
            },
        }

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=100",
            "-c response_schema_conformance",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_with_body_parameters(cli, app_runner, snapshot_cli, ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/projects": {
                "post": {
                    "operationId": "createProject",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                                        "required": ["id", "name"],
                                    }
                                }
                            },
                            "links": {
                                "AddTask": {
                                    "operationId": "createTask",
                                    "parameters": {"project_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/tasks": {
                "post": {
                    "operationId": "createTask",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "title": {"type": "string"},
                                    },
                                    "required": ["project_id", "title"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "project_id": {"type": "string"},
                                            "title": {"type": "string"},
                                        },
                                        "required": ["id", "project_id", "title"],
                                    }
                                }
                            },
                        },
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

    port = app_runner.run_flask_app(app)

    config = {
        "phases": {
            "fuzzing": {"extra-data-sources": {"responses": True}},
        },
    }

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=100",
            "-c response_schema_conformance",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("extra_data_enabled", [True, False])
def test_extra_data_sources_with_response_examples_prepopulation(
    cli, app_runner, snapshot_cli, ctx, extra_data_enabled
):
    known_user_id = "seeded-user-abc-123"
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                                        "required": ["id", "name"],
                                    },
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
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                                        "required": ["id", "name"],
                                    }
                                }
                            },
                        },
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

    port = app_runner.run_flask_app(app)

    config = {
        "phases": {
            "fuzzing": {
                "operation-ordering": "none",
                "extra-data-sources": {"responses": extra_data_enabled},
            },
        },
    }

    # With extra_data_enabled=True, response examples are pre-populated and
    # the bug in GET /users/{user_id} is discovered. With False, only 404s occur.
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=50",
            "-c response_schema_conformance",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


_POST_ITEMS_SCHEMA = {
    "operationId": "createItem",
    "requestBody": {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "examples": {"valid": {"value": {"name": "test-item"}}},
            }
        },
        "required": True,
    },
    "responses": {
        "201": {
            "description": "Created",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                        "required": ["id", "name"],
                    }
                }
            },
            "links": {
                "GetItemById": {
                    "operationId": "getItem",
                    "parameters": {"id": "$response.body#/id"},
                }
            },
        }
    },
}


def _run_items_app(ctx, app_runner, get_params):
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

    return app_runner.run_flask_app(app)


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_examples_phase_merges_pool_with_schema_examples(cli, app_runner, snapshot_cli, ctx):
    # `filter` has a schema example (keeps the phase active), `id` has none.
    # Pool supplies the real `id` from POST, exposing the bug.
    get_params = [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
        {"name": "filter", "in": "query", "schema": {"type": "string", "example": "active"}},
    ]
    port = _run_items_app(ctx, app_runner, get_params)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=examples",
            "-c not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_examples_phase_uses_pool_as_fill_missing_source(cli, app_runner, snapshot_cli, ctx):
    # No schema examples - GET would be skipped without fill-missing.
    # With fill-missing=true, pool supplies the real `id` and exposes the bug.
    get_params = [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}]
    port = _run_items_app(ctx, app_runner, get_params)

    config = {"phases": {"examples": {"fill-missing": True}}}

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=examples",
            "-c not_a_server_error",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_with_examples_and_fuzzing_phases(cli, app_runner, snapshot_cli, ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"email": {"type": "string"}},
                                    "required": ["email"],
                                },
                                "examples": {"valid": {"value": {"email": "test@example.com"}}},
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}, "email": {"type": "string"}},
                                        "required": ["id", "email"],
                                    }
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
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}, "email": {"type": "string"}},
                                        "required": ["id", "email"],
                                    }
                                }
                            },
                        },
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

    port = app_runner.run_flask_app(app)

    config = {
        "phases": {
            "fuzzing": {"extra-data-sources": {"responses": True}},
        },
    }

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=examples,fuzzing",
            "--max-examples=50",
            "-c response_schema_conformance",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_examples_phase_uses_pool_for_body_fields(cli, app_runner, snapshot_cli, ctx):
    # Without body-side pool consumption, fill-missing has nothing to fill the body-only consumer with.
    session_schema = {
        "type": "object",
        "required": ["sessionId"],
        "properties": {"sessionId": {"type": "string", "format": "uuid"}},
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/sessions": {
                "post": {
                    "operationId": "createSession",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": session_schema,
                                "examples": {"valid": {"value": {"sessionId": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"}}},
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": session_schema}},
                        }
                    },
                }
            },
            "/events/log": {
                "post": {
                    "operationId": "logEvent",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": session_schema}},
                    },
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

    port = app_runner.run_flask_app(app)
    config = {"phases": {"examples": {"fill-missing": True}}}

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=examples",
            "-c not_a_server_error",
            "--mode=positive",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_pool_captures_ids_from_multi_array_root_get_list_response(cli, app_runner, snapshot_cli, ctx):
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
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "Volumes": {"type": "array", "items": volume_schema},
                                            "Warnings": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["Volumes"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/volumes/{Name}": {
                "get": {
                    "operationId": "getVolume",
                    "parameters": [
                        {
                            "name": "Name",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": volume_schema}},
                        },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=30",
            "--mode=positive",
            "--seed=42",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_parent_aware_pool_correlates_path_params(cli, app_runner, snapshot_cli, ctx):
    # Deep path planted bug fires only when (productName, itemName) co-refer.
    # Without parent-aware draws, pool feeds independent values and the bug stays unreached.
    products: set[str] = set()
    items: set[tuple[str, str]] = set()
    spec = {
        "/products/{productName}": {
            "post": {
                "operationId": "createProduct",
                "parameters": [
                    {
                        "name": "productName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "responses": {"201": {"description": "Created"}, "409": {"description": "Already exists"}},
            }
        },
        "/products/{productName}/items/{itemName}": {
            "post": {
                "operationId": "createItem",
                "parameters": [
                    {
                        "name": "productName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    },
                    {
                        "name": "itemName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    },
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
                    {
                        "name": "productName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    },
                    {
                        "name": "itemName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=100",
            "--mode=positive",
            "--seed=42",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
def test_post_delete_pool_does_not_re_feed_deleted_ids(cli, app_runner, ctx):
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
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string", "format": "uuid"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                },
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
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

    port = app_runner.run_flask_app(app)
    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        "--phases=fuzzing",
        "--max-examples=100",
        "--mode=positive",
        "--seed=42",
    )

    # Tombstone+eviction must keep stale-id GETs near zero
    assert stale_get_calls <= 5, f"pool kept feeding deleted ids: {stale_get_calls} stale GETs"


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("producer_shape", "wrap_response"),
    [
        ("top-level-array", lambda items: items),
        ("wrapped-array", lambda items: {"data": items}),
    ],
    ids=["top-level-array", "wrapped-array"],
)
def test_stateful_reaches_every_list_producer_element(
    cli, app_runner, snapshot_cli, ctx, producer_shape, wrap_response
):
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
            "Widget": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "label": {"type": "string"}},
                "required": ["id", "label"],
            }
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
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": list_schema}},
                        }
                    },
                }
            },
            "/widgets/{widgetId}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [{"name": "widgetId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"$ref": widget_ref}}},
                        },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "--max-examples=30",
            "--mode=positive",
            "--seed=42",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
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
    app_runner,
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
            sub_resource: {
                "type": "object",
                "properties": sub_props,
                "required": list(sub_props),
            },
            "Post": {
                "type": "object",
                "properties": {"id": {"type": "string"}, sub_field: sub_property},
                "required": ["id", sub_field],
            },
        }
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/posts": {
                "get": {
                    "operationId": "listPosts",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "data": {
                                                "type": "array",
                                                "items": {"$ref": "#/components/schemas/Post"},
                                            }
                                        },
                                        "required": ["data"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            consumer_path: {
                "get": {
                    "operationId": consumer_op,
                    "parameters": [{"name": sub_param, "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"$ref": sub_schema_ref}}},
                        },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--max-examples=30",
            "--mode=positive",
            "--seed=42",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_overlays_nested_body_foreign_key(cli, app_runner, snapshot_cli, ctx):
    # Server returns 500 only when shipping.location_id matches a real Location id;
    # without the nested overlay random ints never hit a real id and the bug stays hidden.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/a/locations": {
                "post": {
                    "operationId": "createLocation",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                        "example": {"id": 11},
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/b/departments": {
                "post": {
                    "operationId": "createDepartment",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "shipping": {
                                            "type": "object",
                                            "properties": {
                                                "location_id": {"type": "integer"},
                                                "note": {"type": "string"},
                                            },
                                        },
                                    },
                                    "required": ["shipping"],
                                }
                            }
                        },
                    },
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

    port = app_runner.run_flask_app(app)
    config = {
        "phases": {
            "fuzzing": {"extra-data-sources": {"responses": True}},
        },
    }
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=coverage,fuzzing",
            "--max-examples=30",
            "--mode=positive",
            "--seed=42",
            "-c not_a_server_error",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.1")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_handles_boolean_body_schema(cli, app_runner, snapshot_cli, ctx):
    # A `schema: true` body alongside pool-using operations must not derail generation.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/widgets/{id}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/anything": {
                "post": {
                    "operationId": "postAnything",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": True}},
                    },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--max-examples=5",
            "--mode=positive",
            "--seed=42",
        )
        == snapshot_cli
    )
