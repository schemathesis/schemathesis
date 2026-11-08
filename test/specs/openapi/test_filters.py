import pytest

import schemathesis

SECURITY_SCHEMES = {"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}}


@pytest.mark.parametrize(
    ("operation", "schema_kwargs", "scheme", "expected"),
    [
        ({"security": [{"session": []}]}, {"components": SECURITY_SCHEMES}, "session", True),
        ({"security": [{"session": []}]}, {"components": SECURITY_SCHEMES}, "other", False),
        ({}, {"security": [{"session": []}], "components": SECURITY_SCHEMES}, "session", True),
        ({}, {}, "session", False),
    ],
    ids=["operation-level-match", "operation-level-no-match", "schema-level-fallback", "no-security"],
)
def test_require_security_scheme(ctx, case_factory, operation, schema_kwargs, scheme, expected):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {**operation, "responses": {"200": {"description": "OK"}}}}}, **schema_kwargs
    )
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme(scheme)(case) is expected
