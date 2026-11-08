from __future__ import annotations

import pytest
from flask import Response, jsonify, request

import schemathesis
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.specs.openapi.checks import (
    _coerce_header_value,
    response_headers_conformance,
    response_schema_conformance,
)
from test.apps.runtime import OpenAPIApp
from test.utils import EventStream

SECURITY_DEFINITIONS = {
    "api_key": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
    "basic_auth": {"type": "basic"},
}
OK_RESPONSE = {"200": {"description": "OK"}}


def test_baseline_e2e(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/baseline": {
                "get": {
                    "produces": ["application/json"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {
                                "type": "object",
                                "properties": {"ok": {"type": "boolean"}},
                                "required": ["ok"],
                            },
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/baseline")
    def baseline():
        return jsonify({"ok": True})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()

    api_calls = api.calls_under("/api/")
    assert api_calls
    assert {call.path for call in api_calls} == {"/api/baseline"}


def test_formdata_serialised_as_multipart(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/upload": {
                "post": {
                    "consumes": ["multipart/form-data"],
                    "produces": ["application/json"],
                    "parameters": [
                        {"name": "title", "in": "formData", "type": "string", "required": True, "minLength": 1},
                        {"name": "file", "in": "formData", "type": "file", "required": True},
                    ],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/upload", methods=["POST"])
    def upload():
        return jsonify({"title": request.form.get("title"), "files": list(request.files.keys())})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    uploads = api.calls_to("/api/upload")
    assert uploads
    for call in uploads:
        assert call.headers.get("Content-Type", "").startswith("multipart/form-data")


def test_collection_format_serialization(ctx):
    delimiters = {"csv": ",", "ssv": " ", "tsv": "\t", "pipes": "|"}
    app, spec = ctx.openapi.make_flask_app(
        {
            "/search": {
                "get": {
                    "produces": ["application/json"],
                    "parameters": [
                        {
                            "name": name,
                            "in": "query",
                            "type": "array",
                            "items": {"type": "string"},
                            "collectionFormat": name,
                            "required": True,
                        }
                        for name in [*delimiters, "multi"]
                    ],
                    "responses": {
                        "200": {"description": "OK", "schema": {"type": "array", "items": {"type": "string"}}}
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/search")
    def search():
        return jsonify(
            {
                "csv": request.query_string.decode().split("&"),
                "raw": {key: request.args.getlist(key) for key in delimiters},
            }
        )

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=20, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_to("/api/search")
    assert captured

    seen = dict.fromkeys(delimiters, False)
    for call in captured:
        for name, delimiter in delimiters.items():
            value = call.query.get(name)
            if value and delimiter in value:
                seen[name] = True
    missing = [name for name, hit in seen.items() if not hit]
    assert not missing, f"Did not observe delimited array for collectionFormat={missing}"
    # `collectionFormat: multi` repeats the key (?multi=a&multi=b) instead of delimiting.
    assert any(call.raw_query.count("multi=") >= 2 for call in captured)


def test_security_credentials_propagate(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/private/api-key": {
                "get": {"security": [{"api_key": []}], "produces": ["application/json"], "responses": OK_RESPONSE}
            },
            "/private/basic": {
                "get": {"security": [{"basic_auth": []}], "produces": ["application/json"], "responses": OK_RESPONSE}
            },
        },
        version="2.0",
        securityDefinitions=SECURITY_DEFINITIONS,
    )

    @app.route("/api/private/api-key")
    def api_key():
        return jsonify({"key": request.headers.get("X-API-Key", "")})

    @app.route("/api/private/basic")
    def basic():
        return jsonify({"auth": request.headers.get("Authorization", "")})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, headers={"X-API-Key": "secret"}, auth=("user", "pass")).execute()

    # Engine also drives auth-omitted negatives; require at least one positive carry per scheme.
    assert any(call.headers.get("X-Api-Key") == "secret" for call in api.calls_to("/api/private/api-key"))
    assert any(call.headers.get("Authorization") == "Basic dXNlcjpwYXNz" for call in api.calls_to("/api/private/basic"))


def test_oauth2_definition_loads(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/oauth-protected": {
                "get": {
                    "security": [{"oauth2_implicit": ["read"]}],
                    "produces": ["application/json"],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
        securityDefinitions={
            "oauth2_implicit": {
                "type": "oauth2",
                "flow": "implicit",
                "authorizationUrl": "https://example.com/oauth/authorize",
                "scopes": {"read": "Read access"},
            },
        },
    )

    @app.route("/api/oauth-protected")
    def oauth_protected():
        return jsonify({})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    operations = list(schema.get_all_operations())
    assert all(not isinstance(op, Err) for op in operations)
    EventStream(schema, max_examples=2).execute()


def test_nullable_response_passes_validation(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/nullable/{id}": {
                "get": {
                    "produces": ["application/json"],
                    "parameters": [{"name": "id", "in": "path", "type": "string", "required": True}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "tag": {"type": "string", "x-nullable": True},
                                },
                                "required": ["name", "tag"],
                            },
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/nullable/<id>")
    def nullable(id):
        return jsonify({"name": id, "tag": None})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()
    stream.assert_no_failures()


def test_examples_phase_uses_x_example(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/examples": {
                "post": {
                    "consumes": ["application/json"],
                    "produces": ["application/json"],
                    "parameters": [
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                                "x-example": {"name": "from-x-example"},
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"type": "object"},
                            "x-examples": {"default": {"summary": "Default", "value": {"echo": "hi"}}},
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/examples", methods=["POST"])
    def examples():
        return jsonify({"echo": "hi"})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, phases=[PhaseName.EXAMPLES]).execute()

    examples_calls = api.calls_to("/api/examples")
    assert examples_calls
    bodies = [c.json() for c in examples_calls]
    assert {"name": "from-x-example"} in bodies


def test_response_header_validation_only_complains_about_headers(ctx, app_runner):
    # A header that breaks its schema must fail with a header message, not body-schema errors against it.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/headers": {
                "get": {
                    "produces": ["application/json"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"type": "object"},
                            "headers": {"X-Total-Count": {"type": "integer"}},
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/headers")
    def headers():
        response = jsonify({})
        response.headers["X-Total-Count"] = "not-a-number"
        return response

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    stream = EventStream(schema, checks=[response_headers_conformance], max_examples=1).execute()
    failures = [
        str(check.failure_info.failure)
        for finished in stream.find_all(events.ScenarioFinished)
        for checks in finished.recorder.checks.values()
        for check in checks
        if check.status == Status.FAILURE
    ]
    assert failures == [
        'Response header does not conform to the schema\n\n"not-a-number" is not of type "integer"\n\n'
        'Schema:\n\n    {\n        "type": "integer"\n    }\n\nValue:\n\n    "not-a-number"'
    ]


def test_default_response_used_when_status_undocumented(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/errors": {
                "get": {
                    "produces": ["application/json"],
                    "responses": {
                        "default": {
                            "description": "Error",
                            "schema": {
                                "type": "object",
                                "properties": {"code": {"type": "integer"}, "message": {"type": "string"}},
                                "required": ["code", "message"],
                            },
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/errors")
    def errors():
        return Response('{"code": 418, "message": "I am a teapot"}', status=418, content_type="application/json")

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()
    stream.assert_no_failures()
    interactions = list(stream.find(events.ScenarioFinished, status=Status.SUCCESS).recorder.interactions.values())
    assert any(i.response.status_code == 418 for i in interactions)


PATH_PARAMETER_CASES = [
    pytest.param(
        {
            "/items/{ids}": {
                "parameters": [
                    {
                        "name": "ids",
                        "in": "path",
                        "type": "array",
                        "items": {"type": "string"},
                        "collectionFormat": "csv",
                        "required": True,
                    }
                ],
                "get": {"produces": ["application/json"], "responses": OK_RESPONSE},
            }
        },
        "/api/items/",
        id="declared-array",
    ),
    # `{name}` is in the path template but not declared in `parameters`.
    pytest.param(
        {"/auto/{name}": {"get": {"produces": ["application/json"], "responses": OK_RESPONSE}}},
        "/api/auto/",
        id="undeclared-injected",
    ),
]


@pytest.mark.parametrize(("paths", "path_prefix"), PATH_PARAMETER_CASES)
def test_path_parameter_reaches_endpoint(ctx, paths, path_prefix):
    app, spec = ctx.openapi.make_flask_app(paths, version="2.0")
    app.add_url_rule(f"{path_prefix}<value>", view_func=lambda value: jsonify({"value": value}))

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()
    assert api.calls_under(path_prefix)


def test_all_parameter_locations_resolve(ctx):
    # Path + query + header + body ($ref'd) must all generate and reach the server in one shot.
    app, spec = ctx.openapi.make_flask_app(
        {
            "/all/{path_param}": {
                "post": {
                    "consumes": ["application/json"],
                    "produces": ["application/json"],
                    "parameters": [
                        {"name": "path_param", "in": "path", "type": "string", "required": True},
                        {"name": "query_param", "in": "query", "type": "integer", "required": True},
                        {"name": "X-Header-Param", "in": "header", "type": "string", "required": True, "minLength": 1},
                        {"name": "body", "in": "body", "required": True, "schema": {"$ref": "#/definitions/Payload"}},
                    ],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
        definitions={
            "Payload": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
        },
    )

    @app.route("/api/all/<path_param>", methods=["POST"])
    def all_locations(path_param):
        return jsonify(
            {
                "path": path_param,
                "query": request.args.get("query_param"),
                "header": request.headers.get("X-Header-Param"),
                "body": request.get_json(silent=True),
            }
        )

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_under("/api/all/")
    assert captured
    for call in captured:
        assert "query_param" in call.query
        assert call.headers.get("X-Header-Param")


INVALID_SCHEMAS = [
    pytest.param(
        {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/x": {
                    "get": {"parameters": {"name": "id", "in": "query"}, "responses": {"200": {"description": "OK"}}}
                }
            },
        },
        id="parameters-as-dict",
    ),
    pytest.param(
        {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {"/x": {"get": {"responses": "not-an-object"}}},
        },
        id="responses-as-string",
    ),
]


@pytest.mark.parametrize("raw", INVALID_SCHEMAS)
def test_invalid_schema_surfaces_invalid_schema_error(ctx, raw):
    schema = schemathesis.openapi.from_dict(raw)
    operations = list(schema.get_all_operations())
    assert operations
    assert all(isinstance(op, Err) for op in operations)
    assert isinstance(operations[0].err(), InvalidSchema)


def test_no_response_body_validation_passes(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/no-content": {
                "delete": {"produces": ["application/json"], "responses": {"204": {"description": "No Content"}}}
            }
        },
        version="2.0",
    )

    @app.route("/api/no-content", methods=["DELETE"])
    def no_content():
        return Response(status=204)

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()
    stream.assert_no_failures()
    assert api.calls_to("/api/no-content")


def test_native_examples_phase_loads(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "produces": ["application/json"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {
                                "type": "array",
                                "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                            },
                            "examples": {"application/json": [{"id": 1}, {"id": 2}]},
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/items")
    def items():
        return jsonify([{"id": 1}, {"id": 2}])

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, phases=[PhaseName.EXAMPLES, PhaseName.FUZZING], max_examples=3).execute()
    assert api.calls_to("/api/items")


def test_optional_security_does_not_require_credentials(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {"/private/optional": {"get": {"security": [], "produces": ["application/json"], "responses": OK_RESPONSE}}},
        version="2.0",
        securityDefinitions=SECURITY_DEFINITIONS,
    )

    @app.route("/api/private/optional")
    def optional():
        return jsonify({"auth": request.headers.get("Authorization", "")})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3).execute()

    optional_calls = api.calls_to("/api/private/optional")
    # Engine reaches the endpoint even without credentials configured.
    assert any("Authorization" not in call.headers for call in optional_calls)


def test_parameter_ref_resolves(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/listing": {
                "get": {
                    "produces": ["application/json"],
                    "parameters": [{"$ref": "#/parameters/Pagination"}],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
        parameters={"Pagination": {"name": "page", "in": "query", "type": "integer", "minimum": 1, "required": True}},
    )

    @app.route("/api/listing")
    def listing():
        return jsonify({"page": request.args.get("page")})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    listings = api.calls_to("/api/listing")
    assert listings
    assert all("page" in call.query for call in listings)


def test_path_level_parameters_apply_to_each_operation(ctx):
    # Path-item-level `parameters` are inherited by every operation under the path.
    app, spec = ctx.openapi.make_flask_app(
        {
            "/path-shared/{token}": {
                "parameters": [
                    {"name": "token", "in": "path", "type": "string", "required": True, "minLength": 1},
                    {"name": "trace", "in": "query", "type": "string", "required": True, "minLength": 1},
                ],
                "get": {"produces": ["application/json"], "responses": OK_RESPONSE},
                "post": {"produces": ["application/json"], "responses": OK_RESPONSE},
            }
        },
        version="2.0",
    )

    @app.route("/api/path-shared/<token>", methods=["GET", "POST"])
    def path_shared(token):
        return jsonify({"token": token, "trace": request.args.get("trace")})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    for method in ("GET", "POST"):
        captured = api.calls_under("/api/path-shared/", method=method)
        assert captured, method
        assert all("trace" in call.query for call in captured), method


def test_form_urlencoded_body_uses_urlencoding(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/form-urlencoded": {
                "post": {
                    "consumes": ["application/x-www-form-urlencoded"],
                    "produces": ["application/json"],
                    "parameters": [
                        {"name": "field_a", "in": "formData", "type": "string", "required": True, "minLength": 1},
                        {"name": "field_b", "in": "formData", "type": "integer", "required": True},
                    ],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/form-urlencoded", methods=["POST"])
    def form_urlencoded():
        return jsonify({"field_a": request.form.get("field_a"), "field_b": request.form.get("field_b")})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    posts = api.calls_to("/api/form-urlencoded")
    assert posts
    for call in posts:
        assert call.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded")


def test_multiple_path_parameters_resolve(ctx):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/orgs/{org_id}/users/{user_id}": {
                "get": {
                    "produces": ["application/json"],
                    "parameters": [
                        {"name": "org_id", "in": "path", "type": "string", "required": True, "minLength": 1},
                        {"name": "user_id", "in": "path", "type": "string", "required": True, "minLength": 1},
                    ],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/orgs/<org_id>/users/<user_id>")
    def org_user(org_id, user_id):
        return jsonify({"org_id": org_id, "user_id": user_id})

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_under("/api/orgs/")
    assert captured
    for call in captured:
        # Path: /api/orgs/{org_id}/users/{user_id} — both segments must be filled.
        segments = call.path.split("/")
        assert len(segments) == 6
        assert segments[3] and segments[5]


def test_diverse_response_headers_accepted(ctx):
    # Integer / boolean / date-time string headers all validate cleanly.
    app, spec = ctx.openapi.make_flask_app(
        {
            "/diverse-headers": {
                "get": {
                    "produces": ["application/json"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"type": "object"},
                            "headers": {
                                "X-Rate-Limit-Remaining": {"type": "integer", "minimum": 0},
                                "X-Deprecated": {"type": "boolean"},
                                "X-Timestamp": {"type": "string", "format": "date-time"},
                            },
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/diverse-headers")
    def diverse_headers():
        response = jsonify({})
        response.headers["X-Rate-Limit-Remaining"] = "42"
        response.headers["X-Deprecated"] = "false"
        response.headers["X-Timestamp"] = "2026-01-01T00:00:00Z"
        return response

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_headers_conformance], max_examples=3).execute()
    stream.assert_no_failures()


def test_array_response_header_with_collection_format(ctx):
    # Swagger 2.0 array response header serialised via collectionFormat: csv —
    # the validator must split on the delimiter before evaluating against `type: array`.
    app, spec = ctx.openapi.make_flask_app(
        {
            "/array-header": {
                "get": {
                    "produces": ["application/json"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"type": "object"},
                            "headers": {
                                "X-Tags": {"type": "array", "items": {"type": "string"}, "collectionFormat": "csv"}
                            },
                        }
                    },
                }
            }
        },
        version="2.0",
    )

    @app.route("/api/array-header")
    def array_header():
        response = jsonify({})
        response.headers["X-Tags"] = "alpha,beta"
        return response

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_headers_conformance], max_examples=3).execute()
    stream.assert_no_failures()


COLLECTION_FORMAT_HEADER_CASES = [
    pytest.param("csv", ",", id="csv"),
    pytest.param("ssv", " ", id="ssv"),
    pytest.param("tsv", "\t", id="tsv"),
    pytest.param("pipes", "|", id="pipes"),
]


@pytest.mark.parametrize(("collection_format", "delimiter"), COLLECTION_FORMAT_HEADER_CASES)
def test_array_response_header_collection_format_unit(collection_format, delimiter):
    # Each collectionFormat splits on the spec-defined delimiter and validates element-wise.
    schema = {"type": "array", "items": {"type": "integer"}, "collectionFormat": collection_format}
    coerced = _coerce_header_value(delimiter.join(["1", "2", "3"]), schema)
    assert coerced == [1, 2, 3]


def test_array_response_header_unknown_collection_format_passthrough():
    # `multi` (and any unrecognised format) is not splittable on a single header line; keep raw.
    schema = {"type": "array", "items": {"type": "string"}, "collectionFormat": "multi"}
    assert _coerce_header_value("a,b", schema) == "a,b"


def test_and_security_carries_both_credentials(ctx):
    # `security: [{api_key: [], basic_auth: []}]` requires both schemes on a single request.
    app, spec = ctx.openapi.make_flask_app(
        {
            "/private/and": {
                "get": {
                    "security": [{"api_key": [], "basic_auth": []}],
                    "produces": ["application/json"],
                    "responses": OK_RESPONSE,
                }
            }
        },
        version="2.0",
        securityDefinitions=SECURITY_DEFINITIONS,
    )

    @app.route("/api/private/and")
    def private_and():
        return jsonify(
            {"api_key": request.headers.get("X-API-Key", ""), "auth": request.headers.get("Authorization", "")}
        )

    api = ctx.openapi.serve(OpenAPIApp(spec=spec, server=app))
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, headers={"X-API-Key": "k"}, auth=("u", "p"), max_examples=3).execute()

    captured = api.calls_to("/api/private/and")
    assert any(
        call.headers.get("X-Api-Key") == "k" and call.headers.get("Authorization") == "Basic dTpw" for call in captured
    )
