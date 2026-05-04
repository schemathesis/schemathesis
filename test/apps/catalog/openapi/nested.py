from __future__ import annotations

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp


def deep_leaf_bug() -> OpenAPIApp:
    spec = build_schema(
        schemas.deep_leaf_bug(),
        components={"schemas": schemas.deep_leaf_bug_components()},
    )
    app = make_flask_app_from_schema(spec)
    handlers.register_deep_leaf_bug(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def header_constraint_bug() -> OpenAPIApp:
    spec = build_schema(schemas.header_constraint_bug())
    app = make_flask_app_from_schema(spec)
    handlers.register_header_constraint_bug(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def query_array_items_bug() -> OpenAPIApp:
    spec = build_schema(schemas.query_array_items_bug())
    app = make_flask_app_from_schema(spec)
    handlers.register_query_array_items_bug(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def one_of_branch_bug() -> OpenAPIApp:
    spec = build_schema(schemas.one_of_branch_bug())
    app = make_flask_app_from_schema(spec)
    handlers.register_one_of_branch_bug(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def additional_properties_bug() -> OpenAPIApp:
    spec = build_schema(schemas.additional_properties_bug())
    app = make_flask_app_from_schema(spec)
    handlers.register_additional_properties_bug(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")
