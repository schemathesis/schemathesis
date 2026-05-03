from __future__ import annotations

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp


def success() -> OpenAPIApp:
    spec = build_schema(schemas.success())
    app = make_flask_app_from_schema(spec)
    handlers.register_success(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")
