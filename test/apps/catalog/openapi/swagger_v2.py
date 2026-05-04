"""Swagger 2.0 catalog factories.

Each factory exercises a cluster of v2-specific runtime paths
(`adapter/v2.py`, `_v2` helpers across `responses`/`security`/`formdata`/...
and the `_serialize_swagger2` collectionFormat branches).
"""

from __future__ import annotations

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers_v2, schemas_v2
from test.apps.runtime import OpenAPIApp


def baseline() -> OpenAPIApp:
    spec = build_schema(schemas_v2.baseline(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_baseline(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def formdata() -> OpenAPIApp:
    spec = build_schema(schemas_v2.formdata(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_formdata(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def collection_format() -> OpenAPIApp:
    spec = build_schema(schemas_v2.collection_format(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_collection_format(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def security() -> OpenAPIApp:
    spec = build_schema(
        schemas_v2.security(),
        version="2.0",
        securityDefinitions=schemas_v2.SECURITY_DEFINITIONS,
    )
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_security(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def nullable() -> OpenAPIApp:
    spec = build_schema(schemas_v2.nullable(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_nullable(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def examples() -> OpenAPIApp:
    spec = build_schema(schemas_v2.examples(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_examples(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def response_headers() -> OpenAPIApp:
    spec = build_schema(schemas_v2.response_headers(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_response_headers(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def default_response() -> OpenAPIApp:
    spec = build_schema(schemas_v2.default_response(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_default_response(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def array_path_parameter() -> OpenAPIApp:
    spec = build_schema(schemas_v2.array_path_parameter(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_array_path_parameter(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def injected_path_parameter() -> OpenAPIApp:
    spec = build_schema(schemas_v2.injected_path_parameter(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_injected_path_parameter(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def all_locations() -> OpenAPIApp:
    spec = build_schema(
        schemas_v2.all_locations(),
        version="2.0",
        definitions={"Payload": schemas_v2.PAYLOAD_DEFINITION},
    )
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_all_locations(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def oauth2_security() -> OpenAPIApp:
    spec = build_schema(
        schemas_v2.oauth2_security(),
        version="2.0",
        securityDefinitions=schemas_v2.OAUTH2_SECURITY_DEFINITIONS,
    )
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_oauth2_security(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def no_response_body() -> OpenAPIApp:
    spec = build_schema(schemas_v2.no_response_body(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_no_response_body(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def native_response_examples() -> OpenAPIApp:
    spec = build_schema(schemas_v2.native_response_examples(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_native_response_examples(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def parameter_ref() -> OpenAPIApp:
    spec = build_schema(
        schemas_v2.parameter_ref(),
        version="2.0",
        parameters=schemas_v2.SHARED_PARAMETERS,
    )
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_parameter_ref(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def path_level_parameters() -> OpenAPIApp:
    spec = build_schema(schemas_v2.path_level_parameters(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_path_level_parameters(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def form_urlencoded() -> OpenAPIApp:
    spec = build_schema(schemas_v2.form_urlencoded(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_form_urlencoded(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def multi_path_parameter() -> OpenAPIApp:
    spec = build_schema(schemas_v2.multi_path_parameter(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_multi_path_parameter(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def diverse_response_headers() -> OpenAPIApp:
    spec = build_schema(schemas_v2.diverse_response_headers(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_diverse_response_headers(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def array_response_header() -> OpenAPIApp:
    spec = build_schema(schemas_v2.array_response_header(), version="2.0")
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_array_response_header(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def and_security() -> OpenAPIApp:
    spec = build_schema(
        schemas_v2.and_security(),
        version="2.0",
        securityDefinitions=schemas_v2.SECURITY_DEFINITIONS,
    )
    app = make_flask_app_from_schema(spec)
    handlers_v2.register_and_security(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


_KITCHEN_SINK_FRAGMENTS = (
    "baseline",
    "formdata",
    "collection_format",
    "security",
    "nullable",
    "examples",
    "response_headers",
    "default_response",
    "array_path_parameter",
    "injected_path_parameter",
    "all_locations",
    "oauth2_security",
    "no_response_body",
    "native_response_examples",
    "parameter_ref",
    "path_level_parameters",
    "form_urlencoded",
    "multi_path_parameter",
    "diverse_response_headers",
    "array_response_header",
    "and_security",
)


def kitchen_sink() -> OpenAPIApp:
    paths: dict = {}
    for fragment in _KITCHEN_SINK_FRAGMENTS:
        for path, methods in getattr(schemas_v2, fragment)().items():
            paths.setdefault(path, {}).update(methods)
    security_definitions = {**schemas_v2.SECURITY_DEFINITIONS, **schemas_v2.OAUTH2_SECURITY_DEFINITIONS}
    spec = build_schema(
        paths,
        version="2.0",
        securityDefinitions=security_definitions,
        definitions={"Payload": schemas_v2.PAYLOAD_DEFINITION},
        parameters=schemas_v2.SHARED_PARAMETERS,
    )
    app = make_flask_app_from_schema(spec)
    for fragment in _KITCHEN_SINK_FRAGMENTS:
        getattr(handlers_v2, f"register_{fragment}")(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")
