import schemathesis


def test_require_security_scheme_operation_level_match(ctx, case_factory):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"security": [{"session": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}},
    )
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("session")(case) is True


def test_require_security_scheme_operation_level_no_match(ctx, case_factory):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"security": [{"session": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}},
    )
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("other")(case) is False


def test_require_security_scheme_schema_level_fallback(ctx, case_factory):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}},
        security=[{"session": []}],
        components={"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}},
    )
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("session")(case) is True


def test_require_security_scheme_no_security(ctx, case_factory):
    schema = ctx.openapi.load_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("session")(case) is False
