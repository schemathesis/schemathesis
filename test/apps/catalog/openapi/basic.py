from __future__ import annotations

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp

_BASIC_AUTH_SCHEME = {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}}
_API_KEY_SCHEME = {"securitySchemes": {"api_key": {"type": "apiKey", "name": "X-Token", "in": "header"}}}
_HEISEN_AUTH_SCHEME = {"securitySchemes": {"heisenAuth": {"type": "http", "scheme": "basic"}}}


def success() -> OpenAPIApp:
    spec = build_schema(schemas.success())
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def failure() -> OpenAPIApp:
    spec = build_schema(schemas.failure())
    app = make_flask_app_from_schema(spec)
    handlers.register_failure(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def multiple_failures() -> OpenAPIApp:
    spec = build_schema(schemas.multiple_failures())
    app = make_flask_app_from_schema(spec)
    handlers.register_multiple_failures(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def custom_format() -> OpenAPIApp:
    spec = build_schema(schemas.custom_format())
    app = make_flask_app_from_schema(spec)
    handlers.register_custom_format(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def payload() -> OpenAPIApp:
    spec = build_schema(schemas.payload())
    app = make_flask_app_from_schema(spec)
    handlers.register_payload(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def unsatisfiable() -> OpenAPIApp:
    spec = build_schema(schemas.unsatisfiable())
    app = make_flask_app_from_schema(spec)
    handlers.register_unsatisfiable(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_and_failure() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.failure()})
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_failure(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_failure_multiple_failures_custom_format() -> OpenAPIApp:
    spec = build_schema(
        {**schemas.success(), **schemas.failure(), **schemas.multiple_failures(), **schemas.custom_format()}
    )
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_failure(app)
    handlers.register_multiple_failures(app)
    handlers.register_custom_format(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def failure_multiple_failures_unsatisfiable() -> OpenAPIApp:
    spec = build_schema({**schemas.failure(), **schemas.multiple_failures(), **schemas.unsatisfiable()})
    app = make_flask_app_from_schema(spec)
    handlers.register_failure(app)
    handlers.register_multiple_failures(app)
    handlers.register_unsatisfiable(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def multipart() -> OpenAPIApp:
    spec = build_schema(schemas.multipart())
    app = make_flask_app_from_schema(spec)
    handlers.register_multipart(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def csv_payload() -> OpenAPIApp:
    spec = build_schema(schemas.csv_payload())
    app = make_flask_app_from_schema(spec)
    handlers.register_csv_payload(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def flaky() -> OpenAPIApp:
    spec = build_schema(schemas.flaky())
    app = make_flask_app_from_schema(spec)
    handlers.register_flaky(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def ignored_auth() -> OpenAPIApp:
    # Endpoint declares heisenAuth but the handler ignores it; used to verify the
    # `ignored_auth` check fires when the server doesn't actually require declared credentials.
    spec = build_schema(schemas.ignored_auth(), components=_HEISEN_AUTH_SCHEME)
    app = make_flask_app_from_schema(spec)
    handlers.register_ignored_auth(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


_NODE_DEFINITION = {
    "type": "object",
    "properties": {"children": {"type": "array", "items": {"$ref": "#/x-definitions/Node"}}},
    "required": ["children"],
}


def form() -> OpenAPIApp:
    spec = build_schema(schemas.form())
    app = make_flask_app_from_schema(spec)
    handlers.register_form(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def upload_file() -> OpenAPIApp:
    spec = build_schema(schemas.upload_file())
    app = make_flask_app_from_schema(spec)
    handlers.register_upload_file(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def always_incorrect() -> OpenAPIApp:
    spec = build_schema(schemas.always_incorrect())
    app = make_flask_app_from_schema(spec)
    handlers.register_always_incorrect(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_and_upload_file() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.upload_file()})
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_upload_file(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def upload_file_and_custom_format() -> OpenAPIApp:
    spec = build_schema({**schemas.upload_file(), **schemas.custom_format()})
    app = make_flask_app_from_schema(spec)
    handlers.register_upload_file(app)
    handlers.register_custom_format(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_and_custom_format() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.custom_format()})
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_custom_format(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_failure_unsatisfiable_empty_string() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.failure(), **schemas.unsatisfiable(), **schemas.empty_string()})
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_failure(app)
    handlers.register_unsatisfiable(app)
    handlers.register_empty_string(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def empty() -> OpenAPIApp:
    spec = build_schema(schemas.empty())
    app = make_flask_app_from_schema(spec)
    handlers.register_empty(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def no_operations() -> OpenAPIApp:
    spec = build_schema({})
    app = make_flask_app_from_schema(spec)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def empty_string() -> OpenAPIApp:
    spec = build_schema(schemas.empty_string())
    app = make_flask_app_from_schema(spec)
    handlers.register_empty_string(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def recursive() -> OpenAPIApp:
    spec = {**build_schema(schemas.recursive()), "x-definitions": {"Node": _NODE_DEFINITION}}
    app = make_flask_app_from_schema(spec)
    handlers.register_recursive(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def invalid_response() -> OpenAPIApp:
    spec = build_schema(schemas.invalid_response())
    app = make_flask_app_from_schema(spec)
    handlers.register_invalid_response(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def invalid_path_parameter() -> OpenAPIApp:
    spec = build_schema(schemas.invalid_path_parameter())
    app = make_flask_app_from_schema(spec)
    handlers.register_invalid_path_parameter(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def missing_path_parameter() -> OpenAPIApp:
    # Path declares `{id}` but no `parameters` section — surfaces as a schema error.
    spec = build_schema(schemas.missing_path_parameter())
    app = make_flask_app_from_schema(spec)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def reserved() -> OpenAPIApp:
    spec = build_schema(schemas.reserved())
    app = make_flask_app_from_schema(spec)
    handlers.register_reserved(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def conformance() -> OpenAPIApp:
    spec = build_schema(schemas.conformance())
    app = make_flask_app_from_schema(spec)
    handlers.register_conformance(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def cp866() -> OpenAPIApp:
    spec = build_schema(schemas.cp866())
    app = make_flask_app_from_schema(spec)
    handlers.register_cp866(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def read_only() -> OpenAPIApp:
    spec = build_schema(schemas.read_only(), components=schemas.READ_WRITE_COMPONENTS)
    app = make_flask_app_from_schema(spec)
    handlers.register_read_only(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def write_only() -> OpenAPIApp:
    spec = build_schema(schemas.write_only(), components=schemas.READ_WRITE_COMPONENTS)
    app = make_flask_app_from_schema(spec)
    handlers.register_write_only(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def text() -> OpenAPIApp:
    spec = build_schema(schemas.text())
    app = make_flask_app_from_schema(spec)
    handlers.register_text(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def plain_text_body() -> OpenAPIApp:
    spec = build_schema(schemas.plain_text_body())
    app = make_flask_app_from_schema(spec)
    handlers.register_plain_text_body(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def teapot() -> OpenAPIApp:
    spec = build_schema(schemas.teapot())
    app = make_flask_app_from_schema(spec)
    handlers.register_teapot(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def malformed_json() -> OpenAPIApp:
    spec = build_schema(schemas.malformed_json())
    app = make_flask_app_from_schema(spec)
    handlers.register_malformed_json(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def invalid() -> OpenAPIApp:
    spec = build_schema(schemas.invalid())
    app = make_flask_app_from_schema(spec)
    handlers.register_invalid(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def chunked_success() -> OpenAPIApp:
    """`/api/success` returning JSON via a generator so Werkzeug emits Transfer-Encoding: chunked."""
    spec = build_schema(schemas.success())
    app = make_flask_app_from_schema(spec)
    handlers.register_chunked_success(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_text_and_write_only() -> OpenAPIApp:
    """Composite for filter-combination tests.

    `/api/success` passes, `/api/text` fails on content-type, `/api/write_only` fails on random input.
    """
    spec = build_schema(
        {**schemas.success(), **schemas.text(), **schemas.write_only()},
        components=schemas.READ_WRITE_COMPONENTS,
    )
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_text(app)
    handlers.register_write_only(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_and_text() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.text()})
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_text(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def slow() -> OpenAPIApp:
    spec = build_schema(schemas.slow())
    app = make_flask_app_from_schema(spec)
    handlers.register_slow(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_and_slow() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.slow()})
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_slow(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def headers() -> OpenAPIApp:
    spec = build_schema(schemas.headers(), components=_API_KEY_SCHEME)
    app = make_flask_app_from_schema(spec)
    handlers.register_headers(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def path_variable() -> OpenAPIApp:
    spec = build_schema(schemas.path_variable())
    app = make_flask_app_from_schema(spec)
    handlers.register_path_variable(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def path_variable_and_custom_format() -> OpenAPIApp:
    spec = build_schema({**schemas.path_variable(), **schemas.custom_format()})
    app = make_flask_app_from_schema(spec)
    handlers.register_path_variable(app)
    handlers.register_custom_format(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def basic() -> OpenAPIApp:
    # 401 is intentionally undocumented so test cases hitting the endpoint without
    # auth surface as response-validation failures.
    spec = build_schema(schemas.basic(), components=_BASIC_AUTH_SCHEME)
    app = make_flask_app_from_schema(spec)
    handlers.register_basic(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def success_and_basic() -> OpenAPIApp:
    spec = build_schema({**schemas.success(), **schemas.basic()}, components=_BASIC_AUTH_SCHEME)
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    handlers.register_basic(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


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
