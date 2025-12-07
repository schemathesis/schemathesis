import platform
from xml.etree import ElementTree

import pytest
import yaml
from _pytest.main import ExitCode
from flask import Flask, jsonify, request

from test.utils import flaky


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_default(cli, schema_url, snapshot_cli, workers):
    assert (
        cli.run(
            schema_url,
            "--max-examples=80",
            "-c not_a_server_error",
            f"--workers={workers}",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sanitization(cli, schema_url, tmp_path):
    cassette_path = tmp_path / "output.yaml"
    token = "secret"
    result = cli.run_and_assert(
        schema_url,
        "--phases=stateful",
        "--max-examples=80",
        "-c not_a_server_error",
        f"--header=Authorization: Bearer {token}",
        f"--report-vcr-path={cassette_path}",
        "--max-failures=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert token not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
@flaky(max_runs=5, min_passes=1)
def test_max_failures(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--max-examples=80",
            "--max-failures=2",
            "--generation-database=none",
            "-c not_a_server_error",
            "--phases=fuzzing,stateful",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_with_cassette(tmp_path, cli, schema_url):
    cassette_path = tmp_path / "output.yaml"
    cli.run(
        schema_url,
        "--max-examples=40",
        "--max-failures=1",
        "-c not_a_server_error",
        f"--report-vcr-path={cassette_path}",
    )
    assert cassette_path.exists()
    with cassette_path.open(encoding="utf-8") as fd:
        cassette = yaml.safe_load(fd)
    assert len(cassette["http_interactions"]) >= 20
    assert cassette["seed"] not in (None, "None")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_with_cassette_stateful_only(tmp_path, cli, schema_url):
    cassette_path = tmp_path / "output.yaml"
    cli.run(
        schema_url,
        "--max-examples=5",
        "--max-failures=1",
        "--phases=stateful",
        "-c not_a_server_error",
        f"--report-vcr-path={cassette_path}",
    )
    assert cassette_path.exists()
    with cassette_path.open(encoding="utf-8") as fd:
        cassette = yaml.safe_load(fd)
    for interaction in cassette["http_interactions"]:
        assert interaction["phase"]["name"] == "stateful"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_junit(tmp_path, cli, schema_url):
    junit_path = tmp_path / "junit.xml"
    cli.run_and_assert(
        schema_url,
        "--phases=stateful",
        "--max-examples=80",
        "--max-failures=1",
        "-c not_a_server_error",
        f"--report-junit-path={junit_path}",
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert junit_path.exists()
    tree = ElementTree.parse(junit_path)
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert len(root) == 1
    assert len(root[0]) == 1
    assert root[0][0].attrib["name"] == "Stateful tests"
    assert len(root[0][0]) == 1
    assert root[0][0][0].tag == "failure"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_only(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--phases=stateful",
            "-n 80",
            "-c not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
def test_stateful_only_with_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--url=http://127.0.0.1:1/api",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_filtered_out(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--max-examples=40",
            "--include-path=/success",
            "--max-failures=1",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_proxy_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--proxy=http://127.0.0.1",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_generation_config(cli, mocker, schema_url, snapshot_cli):
    from schemathesis.specs.openapi import _hypothesis

    mocked = mocker.spy(_hypothesis, "from_schema")
    assert (
        cli.run(
            schema_url,
            "--phases=stateful",
            "--max-examples=50",
            "--generation-allow-x00=false",
            "--generation-codec=ascii",
            "--generation-with-security-parameters=false",
            "-c not_a_server_error",
        )
        == snapshot_cli
    )
    from_schema_kwargs = mocked.call_args_list[0].kwargs
    assert from_schema_kwargs["allow_x00"] is False
    assert from_schema_kwargs["codec"] == "ascii"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_keyboard_interrupt(cli, mocker, schema_url, snapshot_cli):
    def mocked(*args, **kwargs):
        raise KeyboardInterrupt

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    assert cli.run(schema_url, "--phases=stateful") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_missing_link(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--phases=stateful") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_not_enough_links(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--phases=stateful", "--include-method=POST") == snapshot_cli


def test_invalid_parameter_reference(app_factory, app_runner, cli, snapshot_cli):
    # When a link references a non-existent parameter
    app = app_factory(invalid_parameter=True)
    port = app_runner.run_flask_app(app)
    assert cli.run(f"http://127.0.0.1:{port}/openapi.json", "--phases=stateful", "-n 1") == snapshot_cli


def test_missing_body_parameter(app_factory, app_runner, cli, snapshot_cli):
    app = app_factory(omit_required_field=True)
    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "-n 30",
            "-c not_a_server_error",
            "--mode=positive",
            config={"phases": {"stateful": {"inference": {"algorithms": []}}}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@flaky(max_runs=3, min_passes=1)
def test_link_requestbody_extraction_fails_when_producer_missing_id(cli, app_runner, snapshot_cli):
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Minimal API", "version": "1.0.0"},
        "paths": {
            "/products": {
                "post": {
                    "operationId": "createProduct",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "enum": ["Product"]},
                                        "price": {"type": "number", "enum": [9.99]},
                                    },
                                    "required": ["name", "price"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created product",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "name": {"type": "string"},
                                            "price": {"type": "number"},
                                        },
                                        "required": ["id", "name", "price"],
                                    }
                                }
                            },
                            # Link: createOrder should take product id from response body
                            "links": {
                                "CreateOrder": {
                                    "operationId": "createOrder",
                                    # Attempt to populate order requestBody from response body id.
                                    # This runtime expression will fail because producer omits `id`.
                                    "requestBody": {"product_id": "$response.body#/id", "quantity": 1},
                                }
                            },
                        }
                    },
                }
            },
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "product_id": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                    },
                                    "required": ["product_id", "quantity"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Order created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }

    # Minimal Flask app that stores products internally but returns a response that omits `id`.
    app = Flask(__name__)
    products = {}
    next_id = 1
    next_order_id = 1

    @app.route("/openapi.json")
    def get_openapi():
        return jsonify(openapi)

    @app.route("/products", methods=["POST"])
    def create_product():
        nonlocal next_id
        data = request.get_json() or {}
        if not isinstance(data, dict):
            return {"error": "Invalid input"}, 400

        name = data.get("name", "Product")
        if not isinstance(name, str) or not name:
            return {"error": "Invalid name"}, 400

        price = data.get("price", 9.99)
        if not isinstance(price, (int, float)):
            return {"error": "Invalid price"}, 400

        product_id = str(next_id)
        next_id += 1

        products[product_id] = {
            "id": product_id,
            "name": name,
            "price": float(price),
        }

        return jsonify({"name": products[product_id]["name"], "price": products[product_id]["price"]}), 201

    @app.route("/orders", methods=["POST"])
    def create_order():
        nonlocal next_order_id
        data = request.get_json() or {}
        if not isinstance(data, dict):
            return {"error": "Invalid input"}, 400

        product_id = data.get("product_id")
        if not isinstance(product_id, str):
            return {"error": "Invalid product_id"}, 400

        if product_id not in products:
            return jsonify({"detail": "product not found"}), 404
        order_id = str(next_order_id)
        next_order_id += 1
        return jsonify({"id": order_id}), 201

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            "--max-examples=5",
            "-c not_a_server_error",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@flaky(max_runs=3, min_passes=1)
@pytest.mark.parametrize("content", ["", "User data as plain text"])
def test_non_json_response(app_factory, app_runner, cli, snapshot_cli, content):
    app = app_factory(return_plain_text=content)
    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "-n 80",
            "--generation-database=none",
            "-c not_a_server_error",
            "--mode=positive",
            config={"phases": {"stateful": {"inference": {"algorithms": []}}}},
        )
        == snapshot_cli
    )


def test_unique_inputs(ctx, cli, snapshot_cli, openapi3_base_url):
    # See GH-2977
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "post": {
                    "responses": {
                        "200": {
                            "links": {"getItem": {"operationId": "GetById"}},
                        }
                    }
                }
            },
            "/item/{id}": {
                "get": {
                    "operationId": "GetById",
                    "responses": {"200": {"descrionn": "Ok"}},
                },
            },
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--phases=stateful",
            "--generation-unique-inputs",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_stateful_statistic=False)
def test_stateful_link_coverage_with_no_parameters_or_body(cli, app_runner, snapshot_cli, ctx):
    session_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    sessions_list_schema = {
        "type": "object",
        "properties": {
            "sessions": {
                "type": "array",
                "items": session_schema,
            }
        },
        "required": ["sessions"],
    }

    schema = ctx.openapi.build_schema(
        {
            "/sessions": {
                "post": {
                    "operationId": "createSession",
                    "responses": {
                        "200": {
                            "description": "Session created",
                            "content": {"application/json": {"schema": session_schema}},
                            "links": {
                                "GetSessions": {
                                    "operationId": "getSessions",
                                }
                            },
                        }
                    },
                },
                "get": {
                    "operationId": "getSessions",
                    "responses": {
                        "200": {
                            "description": "List of sessions",
                            "content": {"application/json": {"schema": sessions_list_schema}},
                        }
                    },
                },
            },
        }
    )

    app = Flask(__name__)
    sessions = {}
    next_id = 1

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

    @app.route("/sessions", methods=["POST"])
    def create_session():
        nonlocal next_id
        session_id = str(next_id)
        next_id += 1
        sessions[session_id] = {"id": session_id}
        return jsonify({"id": session_id}), 200

    @app.route("/sessions", methods=["GET"])
    def get_sessions():
        return jsonify({"sessions": list(sessions.values())}), 200

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            "--max-examples=10",
            "-c not_a_server_error",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
        )
        == snapshot_cli
    )


def test_nested_link_refs(cli, app_runner, snapshot_cli, ctx):
    # GH-3394: Links with nested $refs should be fully resolved
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "get": {
                    "operationId": "getFoo",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                }
                            },
                            "links": {
                                "Top": {"$ref": "#/components/links/Middle"},
                            },
                        }
                    },
                }
            },
            "/foo/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "get": {
                    "operationId": "get-by-id",
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
        version="3.1.0",
        components={
            "links": {
                "Bottom": {
                    "operationId": "get-by-id",
                    "parameters": {"id": "$response.body#/id"},
                },
                "Middle": {"$ref": "#/components/links/Bottom"},
            }
        },
    )

    app = Flask(__name__)
    next_id = 1

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

    @app.route("/foo", methods=["GET"])
    def get_foo():
        nonlocal next_id
        result = {"id": next_id}
        next_id += 1
        return jsonify(result), 200

    @app.route("/foo/<int:id>", methods=["GET"])
    def get_foo_by_id(id):
        return jsonify({"id": id}), 200

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "--max-examples=5",
            "-c not_a_server_error",
        )
        == snapshot_cli
    )
