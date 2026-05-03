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
