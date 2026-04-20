import schemathesis


def test_require_security_scheme_operation_level_match(ctx, case_factory):
    raw = ctx.openapi.build_schema(
        {"/users": {"get": {"security": [{"session": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}},
    )
    schema = schemathesis.openapi.from_dict(raw)
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("session")(case) is True


def test_require_security_scheme_operation_level_no_match(ctx, case_factory):
    raw = ctx.openapi.build_schema(
        {"/users": {"get": {"security": [{"session": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}},
    )
    schema = schemathesis.openapi.from_dict(raw)
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("other")(case) is False


def test_require_security_scheme_schema_level_fallback(ctx, case_factory):
    raw = ctx.openapi.build_schema(
        {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}},
        security=[{"session": []}],
        components={"securitySchemes": {"session": {"type": "http", "scheme": "bearer"}}},
    )
    schema = schemathesis.openapi.from_dict(raw)
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("session")(case) is True


def test_require_security_scheme_no_security(ctx, case_factory):
    raw = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema = schemathesis.openapi.from_dict(raw)
    case = case_factory(operation=schema["/users"]["get"])
    assert schemathesis.openapi.require_security_scheme("session")(case) is False
