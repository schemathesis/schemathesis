import pytest

from schemathesis.specs.openapi.auth_flow.detection import detect_auth_flow
from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec

BEARER = {"BearerAuth": {"type": "http", "scheme": "bearer"}}
API_KEY = {"ApiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"}}
TOKEN_RESPONSE = {"type": "object", "properties": {"access_token": {"type": "string"}}}
EXPECTED = AuthFlowSpec(
    register_operation="POST /register",
    login_operation="POST /login",
    login_path="/login",
    login_media_type="application/json",
    credentials=("password", "username"),
    token_pointer="/access_token",
    target_scheme="BearerAuth",
)


def _body(fields, media_type="application/json"):
    return {
        "content": {
            media_type: {
                "schema": {
                    "type": "object",
                    "required": list(fields),
                    "properties": {name: {"type": "string"} for name in fields},
                }
            }
        }
    }


def _flow(
    *,
    register_path="/register",
    register_method="post",
    register_fields=("username", "password"),
    register_status="200",
    login_path="/login",
    login_fields=("username", "password"),
    login_security=None,
    token_schema=TOKEN_RESPONSE,
):
    login = {
        "requestBody": _body(login_fields),
        "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": token_schema}}}},
    }
    if login_security is not None:
        login["security"] = login_security
    return {
        register_path: {
            register_method: {
                "requestBody": _body(register_fields),
                "responses": {register_status: {"description": "OK"}},
            }
        },
        login_path: {"post": login},
    }


def _detect(ctx, paths, schemes=BEARER, schemas=None):
    components = {"securitySchemes": schemes} if schemes else {}
    if schemas:
        components["schemas"] = schemas
    return detect_auth_flow(ctx.openapi.load_schema(paths, components=components))


def test_detects_sign_up_and_login(ctx):
    assert _detect(ctx, _flow()) == EXPECTED


@pytest.mark.parametrize("path", ["/signup", "/sign-up", "/users", "/api/v1/account/create-user"])
def test_register_path_variants(ctx, path):
    assert _detect(ctx, _flow(register_path=path)).register_operation == f"POST {path}"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"register_method": "put"},
        {"register_fields": ("password", "title")},
        {"register_status": "400"},
        {"register_path": "/admin/users"},
        {"login_fields": ("username", "email"), "register_fields": ("username", "email", "password")},
        {"login_fields": ("password",)},
        {"token_schema": {"type": "object", "properties": {"not_a_token_name": {"type": "string"}}}},
    ],
    ids=[
        "register-not-post",
        "register-one-credential",
        "register-no-2xx",
        "register-admin-area",
        "login-no-shared-secret",
        "login-one-shared-field",
        "no-token-field",
    ],
)
def test_no_flow(ctx, kwargs):
    assert _detect(ctx, _flow(**kwargs)) is None


def test_no_flow_without_token_scheme(ctx):
    assert _detect(ctx, _flow(), schemes=None) is None


@pytest.mark.parametrize("path", ["/signin", "/sign-in", "/auth", "/api/v1/auth/token"])
def test_login_path_variants(ctx, path):
    assert _detect(ctx, _flow(login_path=path)).login_path == path


def test_login_is_not_the_register_operation_under_an_auth_prefix(ctx):
    assert _detect(ctx, _flow(register_path="/auth/register", login_path="/auth/login")).login_operation == (
        "POST /auth/login"
    )


@pytest.mark.parametrize(
    "token_field",
    ["access_token", "accessToken", "jwt", "JWT", "idToken", "sessionToken", "bearer", "authToken"],
)
def test_token_field_variants(ctx, token_field):
    flow = _flow(token_schema={"type": "object", "properties": {token_field: {"type": "string"}}})
    assert _detect(ctx, flow).token_pointer == f"/{token_field}"


def test_nested_token(ctx):
    flow = _flow(token_schema={"type": "object", "properties": {"data": TOKEN_RESPONSE}})
    assert _detect(ctx, flow).token_pointer == "/data/access_token"


def test_token_behind_chained_references(ctx):
    flow = _flow(token_schema={"$ref": "#/components/schemas/AuthResponse"})
    schemas = {
        "AuthResponse": {"type": "object", "properties": {"data": {"$ref": "#/components/schemas/AuthData"}}},
        "AuthData": TOKEN_RESPONSE,
    }
    assert _detect(ctx, flow, schemas=schemas).token_pointer == "/data/access_token"


@pytest.mark.parametrize(
    ("schemes", "login_security", "expected"),
    [
        ({**BEARER, **API_KEY}, [{"BearerAuth": []}, {"ApiKey": []}], "BearerAuth"),
        ({**BEARER, **API_KEY}, [{"ApiKey": []}], "ApiKey"),
        ({**API_KEY, **BEARER}, None, "BearerAuth"),
        (API_KEY, None, "ApiKey"),
    ],
    ids=["declared-prefers-bearer", "declared-api-key", "undeclared-prefers-bearer", "undeclared-api-key"],
)
def test_target_scheme(ctx, schemes, login_security, expected):
    assert _detect(ctx, _flow(login_security=login_security), schemes=schemes).target_scheme == expected


def test_analysis_caches_the_flow(ctx):
    schema = ctx.openapi.load_schema(_flow(), components={"securitySchemes": BEARER})
    assert schema.analysis.auth_flow is schema.analysis.auth_flow


def test_self_referencing_response_without_token(ctx):
    flow = _flow(token_schema={"$ref": "#/components/schemas/User"})
    schemas = {"User": {"type": "object", "properties": {"manager": {"$ref": "#/components/schemas/User"}}}}
    assert _detect(ctx, flow, schemas=schemas) is None


def test_another_sign_up_operation_is_not_the_login(ctx):
    paths = {
        **_flow(register_path="/users", login_path="/auth/register"),
        **_flow(register_path="/users", login_path="/auth/login"),
    }
    assert _detect(ctx, paths).login_operation == "POST /auth/login"


def test_login_operation_is_not_a_sign_up(ctx):
    paths = {
        **_flow(register_path="/users/login", login_path="/auth/token"),
    }
    assert _detect(ctx, paths) is None


def test_templated_login_path(ctx):
    paths = _flow(login_path="/tenants/{tenant}/login")
    paths["/tenants/{tenant}/login"]["post"]["parameters"] = [
        {"name": "tenant", "in": "path", "required": True, "schema": {"type": "string"}}
    ]
    assert _detect(ctx, paths) is None


@pytest.mark.parametrize(
    "extra",
    [
        {
            "/broken": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Missing"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}},
    ],
    ids=["fails-to-load", "without-body"],
)
def test_unrelated_operations_are_skipped(ctx, extra):
    assert _detect(ctx, {**_flow(), **extra}) == EXPECTED


def test_login_body_media_type_without_properties_is_skipped(ctx):
    paths = _flow()
    content = paths["/login"]["post"]["requestBody"]["content"]
    paths["/login"]["post"]["requestBody"]["content"] = {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
        **content,
    }
    assert _detect(ctx, paths) == EXPECTED


def test_token_after_a_nested_object_without_one(ctx):
    token_schema = {
        "type": "object",
        "properties": {
            "user": {"type": "object", "properties": {"id": {"type": "integer"}}},
            **TOKEN_RESPONSE["properties"],
        },
    }
    assert _detect(ctx, _flow(token_schema=token_schema)) == EXPECTED


def test_boolean_property_schemas_are_skipped(ctx):
    token_schema = {"type": "object", "properties": {"anything": True, **TOKEN_RESPONSE["properties"]}}
    schema = ctx.openapi.load_schema(
        _flow(token_schema=token_schema), version="3.1.0", components={"securitySchemes": BEARER}
    )
    assert detect_auth_flow(schema) == EXPECTED


@pytest.mark.parametrize(
    "first_response",
    [
        {"description": "Created"},
        {"description": "Created", "content": {"application/json": {"schema": {"type": "string"}}}},
    ],
    ids=["no-body", "non-object-body"],
)
def test_token_in_a_later_success_response(ctx, first_response):
    paths = _flow()
    responses = paths["/login"]["post"]["responses"]
    paths["/login"]["post"]["responses"] = {"201": first_response, **responses}
    assert _detect(ctx, paths) == EXPECTED
