import pytest
from flask import Flask, Response, jsonify, stream_with_context
from hypothesis import HealthCheck, given, settings
from requests import Request

import schemathesis
from schemathesis.core.deserialization import _deserialize_sse, _parse_sse_events
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import FailureGroup
from schemathesis.core.transport import Response as TransportResponse
from schemathesis.openapi.checks import JsonSchemaError
from schemathesis.specs.openapi.checks import response_schema_conformance

SSE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "data": {
            "type": "string",
            "contentMediaType": "application/json",
            "contentSchema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
            },
        },
    },
    "required": ["data"],
}

SSE_VALUE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "data": {
            "type": "string",
            "contentMediaType": "application/json",
            "contentSchema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    },
    "required": ["data"],
}


def _sse_schema(item_schema, *, version="3.2.0"):
    return {
        "openapi": version,
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": item_schema}},
                        }
                    }
                }
            }
        },
    }


def _sse_app(raw_schema, body):
    app = Flask(__name__)

    @app.route("/openapi.json")
    def _schema():
        return jsonify(raw_schema)

    @app.route("/sse")
    def _sse():
        return Response(body, mimetype="text/event-stream")

    return app


def _call_sse(raw_schema, body):
    app = Flask(__name__)

    @app.route("/sse")
    def _sse():
        return Response(body, mimetype="text/event-stream")

    schema = schemathesis.openapi.from_dict(raw_schema)
    case = schema["/sse"]["GET"].Case()
    return case, case.call(app=app)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_valid_events(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": SSE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        }
    )

    body = 'event: update\ndata: {"value": 42}\n\nevent: update\ndata: {"value": 100}\n\n'
    port = app_runner.run_flask_app(_sse_app(raw_schema, body))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_schema_violation(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": SSE_VALUE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        }
    )
    body = 'event: update\ndata: {"value": 42}\n\nevent: update\ndata: {"value": "not_an_integer"}\n\n'
    port = app_runner.run_flask_app(_sse_app(raw_schema, body))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_multiple_failing_events(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": SSE_VALUE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        }
    )
    body = (
        'event: update\ndata: {"value": "bad"}\n\n'
        'event: update\ndata: {"value": "also_bad"}\n\n'
        'event: update\ndata: {"value": "still_bad"}\n\n'
    )
    port = app_runner.run_flask_app(_sse_app(raw_schema, body))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_multiline_data(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {
                                "text/event-stream": {
                                    "itemSchema": {
                                        "type": "object",
                                        "properties": {
                                            "data": {
                                                "type": "string",
                                                "contentMediaType": "application/json",
                                                "contentSchema": {
                                                    "type": "object",
                                                    "properties": {"a": {"type": "integer"}},
                                                },
                                            },
                                        },
                                        "required": ["data"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    port = app_runner.run_flask_app(_sse_app(raw_schema, 'data: {"a":\ndata:  1}\n\n'))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_fallback_to_schema_key(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"schema": SSE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        }
    )

    port = app_runner.run_flask_app(_sse_app(raw_schema, 'data: {"value": 42}\n\n'))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_empty_stream(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": SSE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        }
    )

    port = app_runner.run_flask_app(_sse_app(raw_schema, ""))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_itemschema_ignored_for_json(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/json": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "JSON response",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"v": {"type": "integer"}}},
                                    "itemSchema": {"type": "object", "properties": {"v": {"type": "string"}}},
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/json")
    def json_endpoint():
        return jsonify({"v": 42})

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_with_openapi_32(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": SSE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        },
        version="3.2.0",
    )

    port = app_runner.run_flask_app(_sse_app(raw_schema, 'data: {"value": 42}\n\n'))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_oneof_polymorphic_events(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {
                                "text/event-stream": {
                                    "itemSchema": {
                                        "type": "object",
                                        "properties": {
                                            "event": {"type": "string"},
                                            "data": {"type": "string"},
                                        },
                                        "required": ["event"],
                                        "oneOf": [
                                            {
                                                "properties": {
                                                    "event": {"enum": ["ping"]},
                                                }
                                            },
                                            {
                                                "properties": {
                                                    "event": {"enum": ["update"]},
                                                    "data": {
                                                        "contentMediaType": "application/json",
                                                        "contentSchema": {
                                                            "type": "object",
                                                            "properties": {"value": {"type": "integer"}},
                                                            "required": ["value"],
                                                        },
                                                    },
                                                }
                                            },
                                        ],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    port = app_runner.run_flask_app(_sse_app(raw_schema, 'event: ping\n\nevent: update\ndata: {"value": 42}\n\n'))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sse_oneof_content_schema_violation(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {
                                "text/event-stream": {
                                    "itemSchema": {
                                        "type": "object",
                                        "properties": {
                                            "event": {"type": "string"},
                                            "data": {"type": "string"},
                                        },
                                        "required": ["event", "data"],
                                        "oneOf": [
                                            {
                                                "properties": {
                                                    "event": {"enum": ["update"]},
                                                    "data": {
                                                        "contentMediaType": "application/json",
                                                        "contentSchema": {
                                                            "type": "object",
                                                            "properties": {"value": {"type": "integer"}},
                                                            "required": ["value"],
                                                        },
                                                    },
                                                }
                                            },
                                        ],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    port = app_runner.run_flask_app(_sse_app(raw_schema, 'event: update\ndata: {"value": "wrong"}\n\n'))
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


def test_sse_rejects_non_json_payload_for_json_content_schema():
    case, response = _call_sse(_sse_schema(SSE_ITEM_SCHEMA), "data: not-json\n\n")

    with pytest.raises(FailureGroup):
        case.validate_response(response, checks=[response_schema_conformance])


def test_sse_streaming_generator_response():
    app = Flask(__name__)

    @app.route("/sse")
    def sse_endpoint():
        def generate():
            yield 'event: update\ndata: {"value": 42}\n\n'
            yield 'event: update\ndata: {"value": 100}\n\n'

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    schema = schemathesis.openapi.from_dict(_sse_schema(SSE_ITEM_SCHEMA))
    case = schema["/sse"]["GET"].Case()
    response = case.call(app=app)
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_content_schema_uses_matched_oneof_branch():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "oneOf": [
            {"properties": {"event": {"enum": ["ping"]}}},
            {
                "properties": {
                    "event": {"enum": ["update"]},
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                }
            },
        ],
    }
    case, response = _call_sse(
        _sse_schema(item_schema), 'event: ping\ndata: pong\n\nevent: update\ndata: {"value": 42}\n\n'
    )
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_anyof_allows_matching_branch_without_content_schema():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "anyOf": [
            {
                "properties": {
                    "event": {"enum": ["update"]},
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                }
            },
            {"properties": {"event": {"type": "string"}}},
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), "event: update\ndata: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_anyof_with_true_branch_does_not_force_content_schema():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "anyOf": [
            True,
            {
                "properties": {
                    "event": {"enum": ["update"]},
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                }
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), "event: update\ndata: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_oneof_with_true_branch_is_disambiguated_by_content_schema():
    item_schema = {
        "type": "object",
        "properties": {"data": {"type": "string"}},
        "required": ["data"],
        "oneOf": [
            True,
            {
                "properties": {
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    }
                }
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), "data: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_oneof_with_true_branch_preserves_exclusivity_when_content_schema_matches():
    item_schema = {
        "type": "object",
        "properties": {"data": {"type": "string"}},
        "required": ["data"],
        "oneOf": [
            True,
            {
                "properties": {
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    }
                }
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'data: {"value": 1}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_property_level_oneof_enforces_content_schema():
    item_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "oneOf": [
                    {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                    {"pattern": "^ok$"},
                ],
            }
        },
        "required": ["data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), "data: not-json\n\n")

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_property_level_oneof_uses_nested_instance_value_for_branch_selection():
    item_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "oneOf": [
                    True,
                    {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                ],
            }
        },
        "required": ["data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), "data: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_content_schema_uses_registered_deserializer():
    media_type = "application/x-int"

    @schemathesis.deserializer(media_type)
    def deserialize_int(_ctx, response):
        return int(response.content.decode("utf-8"))

    item_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "contentMediaType": media_type,
                "contentSchema": {"type": "integer", "minimum": 10},
            }
        },
        "required": ["data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), "data: not-an-int\n\n")

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_anyof_with_matching_non_json_branch_does_not_force_json_content_schema():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "anyOf": [
            {
                "properties": {
                    "event": {"enum": ["update"]},
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    },
                }
            },
            {
                "properties": {
                    "event": {"enum": ["update"]},
                    "data": {
                        "contentMediaType": "text/plain",
                        "contentSchema": {"type": "string"},
                    },
                }
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), "event: update\ndata: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_allof_with_nested_anyof_preserves_or_semantics_for_content_schema():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "allOf": [
            {"properties": {"event": {"enum": ["update"]}}},
            {
                "anyOf": [
                    {
                        "properties": {
                            "data": {
                                "contentMediaType": "application/json",
                                "contentSchema": {
                                    "type": "object",
                                    "properties": {"value": {"type": "integer"}},
                                    "required": ["value"],
                                },
                            }
                        }
                    },
                    {
                        "properties": {
                            "data": {
                                "contentMediaType": "text/plain",
                                "contentSchema": {"type": "string"},
                            }
                        }
                    },
                ]
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), "event: update\ndata: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_content_schema_enforced_in_nested_composed_branch():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "oneOf": [
            {"properties": {"event": {"enum": ["ping"]}}},
            {
                "allOf": [
                    {"properties": {"event": {"enum": ["update"]}}},
                    {
                        "allOf": [
                            {
                                "properties": {
                                    "data": {
                                        "contentMediaType": "application/json",
                                        "contentSchema": {
                                            "type": "object",
                                            "properties": {"value": {"type": "integer"}},
                                            "required": ["value"],
                                        },
                                    }
                                }
                            }
                        ]
                    },
                ]
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'event: update\ndata: {"value": "wrong"}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_nested_oneof_uses_nested_branch_predicate_for_content_schema():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
        "oneOf": [
            {"properties": {"event": {"enum": ["ping"]}}},
            {
                "allOf": [
                    {"properties": {"event": {"enum": ["update", "delete"]}}},
                    {
                        "oneOf": [
                            {
                                "properties": {
                                    "event": {"enum": ["update"]},
                                    "data": {
                                        "contentMediaType": "application/json",
                                        "contentSchema": {
                                            "type": "object",
                                            "properties": {"update_id": {"type": "integer"}},
                                            "required": ["update_id"],
                                        },
                                    },
                                }
                            },
                            {
                                "properties": {
                                    "event": {"enum": ["delete"]},
                                    "data": {
                                        "contentMediaType": "application/json",
                                        "contentSchema": {
                                            "type": "object",
                                            "properties": {"delete_id": {"type": "string"}},
                                            "required": ["delete_id"],
                                        },
                                    },
                                }
                            },
                        ]
                    },
                ]
            },
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'event: update\ndata: {"delete_id": "abc"}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_content_schema_with_ref_in_oneof_branch():
    raw_schema = {
        "openapi": "3.2.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {
                                "text/event-stream": {
                                    "itemSchema": {
                                        "type": "object",
                                        "properties": {
                                            "event": {"type": "string"},
                                            "data": {"type": "string"},
                                        },
                                        "required": ["event", "data"],
                                        "oneOf": [
                                            {"properties": {"event": {"enum": ["ping"]}}},
                                            {
                                                "properties": {
                                                    "event": {"enum": ["update"]},
                                                    "data": {"$ref": "#/components/schemas/UpdateData"},
                                                }
                                            },
                                        ],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "UpdateData": {
                    "type": "string",
                    "contentMediaType": "application/json",
                    "contentSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                }
            }
        },
    }
    case, response = _call_sse(raw_schema, 'event: update\ndata: {"value": "wrong"}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_content_schema_enforces_allof_with_root_constraints():
    item_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "contentMediaType": "application/json",
                "contentSchema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}},
                    "required": ["a"],
                },
            }
        },
        "required": ["data"],
        "allOf": [
            {
                "properties": {
                    "data": {
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "properties": {"b": {"type": "integer"}},
                            "required": ["b"],
                        },
                    }
                }
            }
        ],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'data: {"a": 1}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_item_schema_ref_with_composed_branches_enforces_content_schema():
    raw_schema = {
        "openapi": "3.2.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {
                                "text/event-stream": {
                                    "itemSchema": {"$ref": "#/components/schemas/SSEEvent"},
                                }
                            },
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "SSEEvent": {
                    "type": "object",
                    "properties": {
                        "event": {"type": "string"},
                        "data": {"type": "string"},
                    },
                    "required": ["event", "data"],
                    "oneOf": [
                        {"properties": {"event": {"enum": ["ping"]}}},
                        {
                            "properties": {
                                "event": {"enum": ["update"]},
                                "data": {
                                    "contentMediaType": "application/json",
                                    "contentSchema": {
                                        "type": "object",
                                        "properties": {"value": {"type": "integer"}},
                                        "required": ["value"],
                                    },
                                },
                            }
                        },
                    ],
                }
            }
        },
    }
    case, response = _call_sse(raw_schema, 'event: update\ndata: {"value": "wrong"}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


@pytest.mark.parametrize(
    "item_schema",
    [
        pytest.param(
            {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "contentMediaType": "bad_media_type",
                        "contentSchema": {"type": "object"},
                    }
                },
                "required": ["data"],
            },
            id="malformed_content_media_type",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "contentMediaType": 42,
                        "contentSchema": {"type": "object"},
                    }
                },
                "required": ["data"],
            },
            id="non_string_content_media_type",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "contentMediaType": "application/json",
                        "contentSchema": {"type": 42},
                    }
                },
                "required": ["data"],
            },
            id="invalid_content_schema",
        ),
    ],
)
def test_sse_invalid_schema_reports_invalid_schema(item_schema):
    case, response = _call_sse(_sse_schema(item_schema), 'data: {"value": 42}\n\n')
    with pytest.raises(InvalidSchema):
        case.validate_response(response, checks=[response_schema_conformance])


def test_sse_content_media_type_without_content_schema_skips_validation():
    item_schema = {
        "type": "object",
        "properties": {"data": {"type": "string", "contentMediaType": "application/json"}},
        "required": ["data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), "data: not-json\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_content_schema_true_allows_any_payload():
    item_schema = {
        "type": "object",
        "properties": {"data": {"type": "string", "contentMediaType": "application/json", "contentSchema": True}},
        "required": ["data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'data: "anything"\n\n')
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_content_schema_false_rejects_any_payload():
    item_schema = {
        "type": "object",
        "properties": {"data": {"type": "string", "contentMediaType": "application/json", "contentSchema": False}},
        "required": ["data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'data: {"value": 42}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_validator_is_cached():
    case, response = _call_sse(_sse_schema(SSE_ITEM_SCHEMA), 'data: {"value": 42}\n\n')
    case.validate_response(response, checks=[response_schema_conformance])
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_false_itemschema_takes_precedence_over_schema():
    raw_schema = {
        "openapi": "3.2.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {
                                "text/event-stream": {
                                    "itemSchema": False,
                                    "schema": SSE_ITEM_SCHEMA,
                                }
                            },
                        }
                    }
                }
            }
        },
    }
    case, response = _call_sse(raw_schema, 'data: {"value": 42}\n\n')

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=[response_schema_conformance])

    assert any(isinstance(failure, JsonSchemaError) for failure in exc.value.exceptions)


def test_sse_metadata_only_blocks_are_not_validated_as_events():
    case, response = _call_sse(_sse_schema(SSE_ITEM_SCHEMA), 'id: 1\nretry: 3000\n\ndata: {"value": 42}\n\n')
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_last_event_id_carry_over_satisfies_required_id():
    item_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "data": {"type": "string"},
        },
        "required": ["id", "data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), 'id: 1\n\ndata: {"value": 42}\n\n')
    case.validate_response(response, checks=[response_schema_conformance])


def test_sse_unnamed_events_default_to_message_type():
    item_schema = {
        "type": "object",
        "properties": {
            "event": {"enum": ["message"]},
            "data": {"type": "string"},
        },
        "required": ["event", "data"],
    }
    case, response = _call_sse(_sse_schema(item_schema), "data: hello\n\n")
    case.validate_response(response, checks=[response_schema_conformance])


@pytest.mark.hypothesis_nested
def test_sse_pytest_plugin():
    app = Flask(__name__)

    raw_schema = {
        "openapi": "3.2.0",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/sse": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "SSE stream",
                            "content": {"text/event-stream": {"itemSchema": SSE_ITEM_SCHEMA}},
                        }
                    }
                }
            }
        },
    }

    @app.route("/openapi.json")
    def schema_route():
        return jsonify(raw_schema)

    @app.route("/sse")
    def sse_endpoint():
        return Response('data: {"value": 42}\n\n', mimetype="text/event-stream")

    schema = schemathesis.openapi.from_dict(raw_schema)
    strategy = schema["/sse"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=10, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call(app=app)
        case.validate_response(response)

    test()


@pytest.mark.parametrize(
    "content, expected",
    [
        pytest.param(
            b'event: update\ndata: {"value": 42}\nid: 1\n\nevent: ping\ndata: {}\n\n',
            [
                {"event": "update", "data": '{"value": 42}', "id": "1"},
                {"event": "ping", "data": "{}", "id": "1"},
            ],
            id="multiple-events",
        ),
        pytest.param(
            b'data: {"a":\ndata:  1}\n\n',
            [{"event": "message", "data": '{"a":\n 1}'}],
            id="multiline-data",
        ),
        pytest.param(
            b': this is a comment\ndata: {"x": 1}\n\n',
            [{"event": "message", "data": '{"x": 1}'}],
            id="comments-skipped",
        ),
        pytest.param(
            b"data: hello world\n\n",
            [{"event": "message", "data": "hello world"}],
            id="plain-text-data",
        ),
        pytest.param(
            b"retry: 3000\ndata: {}\n\n",
            [{"event": "message", "retry": "3000", "data": "{}"}],
            id="retry-field",
        ),
        pytest.param(
            b'id: 1\n\ndata: {"x": 1}\n\n',
            [{"event": "message", "id": "1", "data": '{"x": 1}'}],
            id="last-event-id-carry-over",
        ),
        pytest.param(
            b"id: 1\nretry: 3000\n\n",
            [],
            id="metadata-only-block",
        ),
        pytest.param(
            b'data: {"x": 1}',
            [{"event": "message", "data": '{"x": 1}'}],
            id="no-trailing-blank-line",
        ),
        pytest.param(
            b'event: update\r\ndata: {"v": 1}\r\n\r\n',
            [{"event": "update", "data": '{"v": 1}'}],
            id="crlf-line-endings",
        ),
        pytest.param(
            b'\xef\xbb\xbfdata: {"x": 1}\n\n',
            [{"event": "message", "data": '{"x": 1}'}],
            id="utf8-bom",
        ),
        pytest.param(
            b'data: {"v": 2}\r\r',
            [{"event": "message", "data": '{"v": 2}'}],
            id="cr-line-endings",
        ),
        pytest.param(
            b"data\n\n",
            [{"event": "message", "data": ""}],
            id="bare-field-name",
        ),
        pytest.param(
            b"data:  two spaces\n\n",
            [{"event": "message", "data": " two spaces"}],
            id="single-leading-space-stripped",
        ),
        pytest.param(
            b"id: bad\x00id\ndata: hello\n\n",
            [{"event": "message", "data": "hello"}],
            id="id-with-null-char-ignored",
        ),
        pytest.param(
            b"id: first\n\nid: bad\x00id\ndata: hello\n\n",
            [{"event": "message", "data": "hello", "id": "first"}],
            id="id-null-char-falls-back-to-last-event-id",
        ),
        pytest.param(
            b"",
            [],
            id="empty-stream",
        ),
    ],
)
def test_parse_sse_events(content, expected):
    assert _parse_sse_events(content) == expected


def test_deserialize_sse_uses_utf8_ignoring_response_encoding():
    response = TransportResponse(
        status_code=200,
        headers={"content-type": ["text/event-stream"]},
        content="data: café\n\n".encode(),
        request=Request("GET", "http://example.com/sse").prepare(),
        elapsed=0.0,
        verify=True,
        encoding="iso-8859-1",
    )

    assert _deserialize_sse(None, response) == [{"event": "message", "data": "café"}]
