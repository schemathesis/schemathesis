from __future__ import annotations

import time

from fastapi import FastAPI
from flask import jsonify, request
from starlette.types import Receive, Scope, Send

from test.apps.builders import build_schema, flask_app, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp

_BASIC_AUTH_SCHEME = {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}}
_API_KEY_SCHEME = {"securitySchemes": {"api_key": {"type": "apiKey", "name": "X-Token", "in": "header"}}}
_HEISEN_AUTH_SCHEME = {"securitySchemes": {"heisenAuth": {"type": "http", "scheme": "basic"}}}


def success() -> OpenAPIApp:
    return flask_app(schemas.success(), handlers.register_success)


def per_path_servers() -> OpenAPIApp:
    return flask_app(schemas.per_path_servers(), handlers.register_per_path_servers)


def failure() -> OpenAPIApp:
    return flask_app(schemas.failure(), handlers.register_failure)


def multiple_failures() -> OpenAPIApp:
    return flask_app(schemas.multiple_failures(), handlers.register_multiple_failures)


def custom_format() -> OpenAPIApp:
    return flask_app(schemas.custom_format(), handlers.register_custom_format)


def payload() -> OpenAPIApp:
    return flask_app(schemas.payload(), handlers.register_payload)


def unsatisfiable() -> OpenAPIApp:
    return flask_app(schemas.unsatisfiable(), handlers.register_unsatisfiable)


def success_and_failure() -> OpenAPIApp:
    return flask_app({**schemas.success(), **schemas.failure()}, handlers.register_success, handlers.register_failure)


def success_failure_multiple_failures_custom_format() -> OpenAPIApp:
    return flask_app(
        {**schemas.success(), **schemas.failure(), **schemas.multiple_failures(), **schemas.custom_format()},
        handlers.register_success,
        handlers.register_failure,
        handlers.register_multiple_failures,
        handlers.register_custom_format,
    )


def failure_multiple_failures_unsatisfiable() -> OpenAPIApp:
    return flask_app(
        {**schemas.failure(), **schemas.multiple_failures(), **schemas.unsatisfiable()},
        handlers.register_failure,
        handlers.register_multiple_failures,
        handlers.register_unsatisfiable,
    )


def multipart() -> OpenAPIApp:
    return flask_app(schemas.multipart(), handlers.register_multipart)


def csv_payload() -> OpenAPIApp:
    return flask_app(schemas.csv_payload(), handlers.register_csv_payload)


def identical_query_parameters() -> OpenAPIApp:
    return flask_app(schemas.identical_query_parameters(), handlers.register_identical_query_parameters)


def flaky() -> OpenAPIApp:
    return flask_app(schemas.flaky(), handlers.register_flaky)


def ignored_auth() -> OpenAPIApp:
    # Endpoint declares heisenAuth but the handler ignores it; used to verify the
    # `ignored_auth` check fires when the server doesn't actually require declared credentials.
    return flask_app(schemas.ignored_auth(), handlers.register_ignored_auth, components=_HEISEN_AUTH_SCHEME)


_NODE_DEFINITION = {
    "type": "object",
    "properties": {"children": {"type": "array", "items": {"$ref": "#/x-definitions/Node"}}},
    "required": ["children"],
}


def form() -> OpenAPIApp:
    return flask_app(schemas.form(), handlers.register_form)


def upload_file() -> OpenAPIApp:
    return flask_app(schemas.upload_file(), handlers.register_upload_file)


def always_incorrect() -> OpenAPIApp:
    return flask_app(schemas.always_incorrect(), handlers.register_always_incorrect)


def success_and_upload_file() -> OpenAPIApp:
    return flask_app(
        {**schemas.success(), **schemas.upload_file()}, handlers.register_success, handlers.register_upload_file
    )


def upload_file_and_custom_format() -> OpenAPIApp:
    return flask_app(
        {**schemas.upload_file(), **schemas.custom_format()},
        handlers.register_upload_file,
        handlers.register_custom_format,
    )


def success_and_custom_format() -> OpenAPIApp:
    return flask_app(
        {**schemas.success(), **schemas.custom_format()}, handlers.register_success, handlers.register_custom_format
    )


def success_failure_unsatisfiable_empty_string() -> OpenAPIApp:
    return flask_app(
        {**schemas.success(), **schemas.failure(), **schemas.unsatisfiable(), **schemas.empty_string()},
        handlers.register_success,
        handlers.register_failure,
        handlers.register_unsatisfiable,
        handlers.register_empty_string,
    )


def empty() -> OpenAPIApp:
    return flask_app(schemas.empty(), handlers.register_empty)


def no_operations() -> OpenAPIApp:
    return flask_app({})


def empty_string() -> OpenAPIApp:
    return flask_app(schemas.empty_string(), handlers.register_empty_string)


def recursive() -> OpenAPIApp:
    return flask_app(schemas.recursive(), handlers.register_recursive, **{"x-definitions": {"Node": _NODE_DEFINITION}})


def invalid_response() -> OpenAPIApp:
    return flask_app(schemas.invalid_response(), handlers.register_invalid_response)


def invalid_path_parameter() -> OpenAPIApp:
    return flask_app(schemas.invalid_path_parameter(), handlers.register_invalid_path_parameter)


def missing_path_parameter() -> OpenAPIApp:
    # Path declares `{id}` but no `parameters` section — surfaces as a schema error.
    return flask_app(schemas.missing_path_parameter())


def reserved() -> OpenAPIApp:
    return flask_app(schemas.reserved(), handlers.register_reserved)


def conformance() -> OpenAPIApp:
    return flask_app(schemas.conformance(), handlers.register_conformance)


def cp866() -> OpenAPIApp:
    return flask_app(schemas.cp866(), handlers.register_cp866)


def read_only() -> OpenAPIApp:
    return flask_app(schemas.read_only(), handlers.register_read_only, components=schemas.READ_WRITE_COMPONENTS)


def write_only() -> OpenAPIApp:
    return flask_app(schemas.write_only(), handlers.register_write_only, components=schemas.READ_WRITE_COMPONENTS)


def text() -> OpenAPIApp:
    return flask_app(schemas.text(), handlers.register_text)


def plain_text_body() -> OpenAPIApp:
    return flask_app(schemas.plain_text_body(), handlers.register_plain_text_body)


def teapot() -> OpenAPIApp:
    return flask_app(schemas.teapot(), handlers.register_teapot)


def malformed_json() -> OpenAPIApp:
    return flask_app(schemas.malformed_json(), handlers.register_malformed_json)


def invalid() -> OpenAPIApp:
    return flask_app(schemas.invalid(), handlers.register_invalid)


def success_text_and_write_only() -> OpenAPIApp:
    """Composite for filter-combination tests.

    `/api/success` passes, `/api/text` fails on content-type, `/api/write_only` fails on random input.
    """
    return flask_app(
        {**schemas.success(), **schemas.text(), **schemas.write_only()},
        handlers.register_success,
        handlers.register_text,
        handlers.register_write_only,
        components=schemas.READ_WRITE_COMPONENTS,
    )


def success_and_text() -> OpenAPIApp:
    return flask_app({**schemas.success(), **schemas.text()}, handlers.register_success, handlers.register_text)


def slow() -> OpenAPIApp:
    return flask_app(schemas.slow(), handlers.register_slow)


def headers() -> OpenAPIApp:
    return flask_app(schemas.headers(), handlers.register_headers, components=_API_KEY_SCHEME)


def path_variable() -> OpenAPIApp:
    return flask_app(schemas.path_variable(), handlers.register_path_variable)


def array_path_variable() -> OpenAPIApp:
    return flask_app(schemas.array_path_variable(), handlers.register_array_path_variable)


def path_variable_and_custom_format() -> OpenAPIApp:
    return flask_app(
        {**schemas.path_variable(), **schemas.custom_format()},
        handlers.register_path_variable,
        handlers.register_custom_format,
    )


def basic() -> OpenAPIApp:
    # 401 is intentionally undocumented so test cases hitting the endpoint without
    # auth surface as response-validation failures.
    return flask_app(schemas.basic(), handlers.register_basic, components=_BASIC_AUTH_SCHEME)


def basic_with_query() -> OpenAPIApp:
    return flask_app(schemas.basic_with_query(), handlers.register_basic_with_query, components=_BASIC_AUTH_SCHEME)


def success_and_basic() -> OpenAPIApp:
    return flask_app(
        {**schemas.success(), **schemas.basic()},
        handlers.register_success,
        handlers.register_basic,
        components=_BASIC_AUTH_SCHEME,
    )


def schema_unavailable(failures: int = 1) -> OpenAPIApp:
    """The first `failures` schema fetches answer 503, like a service that is still starting."""
    spec = build_schema(schemas.success())
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    remaining = failures

    @app.before_request
    def not_ready() -> object:
        nonlocal remaining
        if request.path == "/openapi.json" and remaining > 0:
            remaining -= 1
            return jsonify({"detail": "not ready"}), 503
        return None

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def sessions_and_log_event() -> OpenAPIApp:
    return flask_app(schemas.sessions_and_log_event(), handlers.register_sessions_and_log_event)


# Every fragment in `test/apps/fragments/schemas.py`, including deliberately-broken ones.
# Used by the local launcher (`python -m test.apps`) to expose everything for manual debugging.
_KITCHEN_SINK_FRAGMENTS = (
    "success",
    "failure",
    "multiple_failures",
    "payload",
    "unsatisfiable",
    "flaky",
    "ignored_auth",
    "multipart",
    "csv_payload",
    "form",
    "upload_file",
    "always_incorrect",
    "empty",
    "empty_string",
    "recursive",
    "invalid_response",
    "invalid_path_parameter",
    "missing_path_parameter",
    "reserved",
    "conformance",
    "cp866",
    "read_only",
    "write_only",
    "text",
    "plain_text_body",
    "teapot",
    "malformed_json",
    "invalid",
    "slow",
    "headers",
    "path_variable",
    "custom_format",
    "basic",
)


def kitchen_sink() -> OpenAPIApp:
    paths: dict = {}
    for fragment in _KITCHEN_SINK_FRAGMENTS:
        for path, methods in getattr(schemas, fragment)().items():
            paths.setdefault(path, {}).update(methods)
    components = {
        "securitySchemes": {
            **_BASIC_AUTH_SCHEME["securitySchemes"],
            **_API_KEY_SCHEME["securitySchemes"],
            **_HEISEN_AUTH_SCHEME["securitySchemes"],
        },
        "schemas": dict(schemas.READ_WRITE_COMPONENTS["schemas"]),
    }
    spec = {**build_schema(paths, components=components), "x-definitions": {"Node": _NODE_DEFINITION}}
    app = make_flask_app_from_schema(spec)
    for fragment in _KITCHEN_SINK_FRAGMENTS:
        register = getattr(handlers, f"register_{fragment}", None)
        if register is not None:
            register(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def collection_with_planted_bug() -> OpenAPIApp:
    """The listing carries the only token that reaches the planted 500; generated ones are rejected."""
    token = "b7e2a41c9f3d4c8a92e51d6f0a7c3b58"
    entry = {"type": "object", "properties": {"token": {"type": "string"}}}
    spec = build_schema(
        {
            "/api/tokens": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "array", "items": entry}}}}
                    }
                }
            },
            "/api/verify": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {**entry, "required": ["token"]}}},
                    },
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Unknown"}},
                }
            },
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/api/tokens", methods=["GET"])
    def list_tokens() -> object:
        return jsonify([{"token": token}])

    @app.route("/api/verify", methods=["POST"])
    def verify() -> object:
        body = request.get_json(silent=True)
        if isinstance(body, dict) and body.get("token") == token:
            raise RuntimeError("boom")
        return jsonify({"detail": "unknown"}), 404

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def undocumented_collection_with_planted_bug() -> OpenAPIApp:
    """The listing names the only tag the search accepts, and the spec declares no response shape."""
    tag = "aurora-borealis-7"
    spec = build_schema(
        {
            "/api/tags": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/search": {
                "get": {
                    "parameters": [{"name": "tag", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Unknown"}},
                }
            },
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/api/tags", methods=["GET"])
    def list_tags() -> object:
        return jsonify([{"tag": tag}])

    @app.route("/api/search", methods=["GET"])
    def search() -> object:
        if request.args.get("tag") == tag:
            raise RuntimeError("boom")
        return jsonify({"detail": "unknown"}), 404

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def vocabulary_path_with_planted_bug() -> OpenAPIApp:
    """The listing names the only genre the catalog accepts; the schema declares no response shape."""
    genre = "post-bop"
    spec = build_schema(
        {
            "/api/genres": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/catalog/{genre}": {
                "get": {
                    "parameters": [{"name": "genre", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Unknown"}},
                }
            },
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/api/genres", methods=["GET"])
    def list_genres() -> object:
        return jsonify([{"genre": genre}])

    @app.route("/api/catalog/<value>", methods=["GET"])
    def catalog(value: str) -> object:
        if value == genre:
            raise RuntimeError("boom")
        return jsonify({"detail": "unknown"}), 404

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def crash_closes_connection() -> OpenAPIApp:
    app = FastAPI()

    @app.post("/api/crash")
    def crash(value: dict) -> dict:
        raise KeyError("boom")

    async def server(scope: Scope, receive: Receive, send: Send) -> None:
        # Stall like uvicorn logging the traceback, so the connection closes after the 500 with the next request unread.
        try:
            await app(scope, receive, send)
        except KeyError:
            time.sleep(0.05)
            raise

    return OpenAPIApp(spec=app.openapi(), server=server, kind="fastapi")
