from __future__ import annotations

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp


def paged_response_albums() -> OpenAPIApp:
    spec = build_schema(schemas.paged_response_albums())
    app = make_flask_app_from_schema(spec)
    handlers.register_paged_response_albums(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def paged_response_albums_declared() -> OpenAPIApp:
    spec = build_schema(schemas.paged_response_albums_declared())
    app = make_flask_app_from_schema(spec)
    handlers.register_paged_response_albums(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")
