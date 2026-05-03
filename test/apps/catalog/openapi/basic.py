from __future__ import annotations

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp

_BASIC_AUTH_SCHEME = {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}}


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
