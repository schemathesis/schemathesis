import uuid

import pytest
from flask import Flask, abort, jsonify, request


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("config_enabled", [False, True])
def test_extra_data_sources_enables_bug_discovery(cli, app_runner, snapshot_cli, ctx, config_enabled):
    item_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)
    items = {}

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

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
    schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)
    projects = {}
    tasks = {}

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

    @app.route("/projects", methods=["POST"])
    def create_project():
        data = request.get_json() or {}
        project_id = uuid.uuid4().hex
        project = {"id": project_id, "name": data.get("name", "Project")}
        projects[project_id] = project
        return jsonify(project), 201

    @app.route("/tasks", methods=["POST"])
    def create_task():
        data = request.get_json() or {}
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
    schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)
    # Simulating a pre-seeded database with a known user
    seeded_users = {known_user_id: {"id": known_user_id, "name": "Seeded User"}}

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

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


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_extra_data_sources_with_examples_and_fuzzing_phases(cli, app_runner, snapshot_cli, ctx):
    schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)
    users = {}

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

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
