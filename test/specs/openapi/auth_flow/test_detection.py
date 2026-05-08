import pytest

import schemathesis
from schemathesis.specs.openapi.auth_flow.detection import (
    detect_auth_flow,
    find_login_for_register,
    find_register_candidates,
    resolve_token_source,
)


def _build_register_operation(*, path="/register", method="post", required=("username", "password")):
    return {
        path: {
            method: {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": list(required),
                                "properties": {name: {"type": "string"} for name in required},
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def test_find_register_simple(ctx):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_build_register_operation()))
    candidates = find_register_candidates(schema)
    assert [c.label for c in candidates] == ["POST /register"]


@pytest.mark.parametrize("path", ["/signup", "/sign-up", "/users", "/api/v1/account/create-user"])
def test_find_register_path_variants(ctx, path):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_build_register_operation(path=path)))
    candidates = find_register_candidates(schema)
    assert len(candidates) == 1


def test_find_register_requires_post(ctx):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_build_register_operation(method="get")))
    assert find_register_candidates(schema) == []


def test_find_register_requires_two_credential_fields(ctx):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(_build_register_operation(required=("password", "title")))
    )
    assert find_register_candidates(schema) == []


def test_find_register_requires_2xx_response(ctx):
    paths = _build_register_operation()
    paths["/register"]["post"]["responses"] = {"400": {"description": "Bad"}}
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths))
    assert find_register_candidates(schema) == []


def _build_register_and_login(
    *,
    login_path="/login",
    login_required=("username", "password"),
    register_required=("username", "password"),
):
    return {
        "/register": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": list(register_required),
                                "properties": {name: {"type": "string"} for name in register_required},
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        login_path: {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": list(login_required),
                                "properties": {name: {"type": "string"} for name in login_required},
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
    }


def test_find_login_pairs_with_register(ctx):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_build_register_and_login()))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    assert login is not None
    assert login.label == "POST /login"


@pytest.mark.parametrize("login_path", ["/signin", "/sign-in", "/auth", "/api/v1/auth/token"])
def test_find_login_path_variants(ctx, login_path):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_build_register_and_login(login_path=login_path)))
    register = find_register_candidates(schema)[0]
    assert find_login_for_register(schema, register) is not None


def test_find_login_requires_secret_field_overlap(ctx):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(
            _build_register_and_login(
                register_required=("username", "email", "password"),
                login_required=("username", "email"),
            )
        )
    )
    register = find_register_candidates(schema)[0]
    assert find_login_for_register(schema, register) is None


def test_find_login_requires_two_field_overlap(ctx):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(_build_register_and_login(login_required=("password",)))
    )
    register = find_register_candidates(schema)[0]
    assert find_login_for_register(schema, register) is None


_BEARER_SCHEME = {
    "components": {"securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}}},
}


def _login_with_response_token(token_field="access_token"):
    paths = _build_register_and_login()
    paths["/login"]["post"]["responses"]["200"] = {
        "description": "OK",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {token_field: {"type": "string"}},
                }
            }
        },
    }
    paths["/login"]["post"]["security"] = [{"BearerAuth": []}]
    return paths


def test_resolve_token_source_body(ctx):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_login_with_response_token(), **_BEARER_SCHEME))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.extract_from == "body"
    assert source.extract_selector == "/access_token"
    assert source.target_scheme == "BearerAuth"


def test_resolve_token_source_nested_pointer(ctx):
    paths = _login_with_response_token()
    paths["/login"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {"access_token": {"type": "string"}},
            }
        },
    }
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **_BEARER_SCHEME))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.extract_selector == "/data/access_token"


def test_resolve_token_source_prefers_bearer_over_apikey(ctx):
    paths = _login_with_response_token()
    paths["/login"]["post"]["security"] = [{"BearerAuth": []}, {"ApiKey": []}]
    schemes = {
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
                "ApiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"},
            }
        }
    }
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **schemes))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.target_scheme == "BearerAuth"


def test_resolve_token_source_falls_back_to_apikey(ctx):
    paths = _login_with_response_token()
    paths["/login"]["post"]["security"] = [{"ApiKey": []}]
    schemes = {
        "components": {
            "securitySchemes": {
                "ApiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"},
            }
        }
    }
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **schemes))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.target_scheme == "ApiKey"


def test_resolve_token_source_no_token_field_returns_none(ctx):
    paths = _login_with_response_token(token_field="not_a_token_name")
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **_BEARER_SCHEME))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    assert resolve_token_source(schema, login) is None


def test_resolve_token_source_no_compatible_scheme_returns_none(ctx):
    paths = _login_with_response_token()
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths))  # no securitySchemes
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    assert resolve_token_source(schema, login) is None


@pytest.mark.parametrize(
    "token_field",
    ["accessToken", "jwt", "JWT", "idToken", "sessionToken", "bearer", "authToken"],
)
def test_resolve_token_source_token_field_variants(ctx, token_field):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(_login_with_response_token(token_field=token_field), **_BEARER_SCHEME)
    )
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.extract_selector == f"/{token_field}"


def test_detect_auth_flow_complete(ctx):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_login_with_response_token(), **_BEARER_SCHEME))
    spec = detect_auth_flow(schema)
    assert spec is not None
    assert spec.register_operation == "POST /register"
    assert spec.login_operation == "POST /login"
    assert spec.target_scheme == "BearerAuth"
    assert {f.name for f in spec.credentials} == {"username", "password"}
    assert spec.token_config.path == "/login"
    assert spec.token_config.extract_selector == "/access_token"


def test_detect_auth_flow_returns_none_when_no_register(ctx):
    paths = _login_with_response_token()
    del paths["/register"]
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **_BEARER_SCHEME))
    assert detect_auth_flow(schema) is None


def test_credential_fields_prefer_stricter_schema(ctx):
    paths = {
        "/register": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {
                                        "type": "string",
                                        "minLength": 12,
                                        "pattern": "^[A-Z].*",
                                    },
                                },
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/login": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"access_token": {"type": "string"}},
                                }
                            }
                        },
                    }
                },
                "security": [{"BearerAuth": []}],
            }
        },
    }
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **_BEARER_SCHEME))
    spec = detect_auth_flow(schema)
    assert spec is not None
    by_name = {field.name: field for field in spec.credentials}
    assert by_name["password"].schema.get("minLength") == 12
    assert by_name["password"].schema.get("pattern") == "^[A-Z].*"


def test_analysis_auth_flow_caches(ctx):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(_login_with_response_token(), **_BEARER_SCHEME))
    spec1 = schema.analysis.auth_flow
    spec2 = schema.analysis.auth_flow
    assert spec1 is spec2


def test_walk_token_via_ref(ctx):
    paths = _build_register_and_login()
    paths["/login"]["post"]["responses"]["200"] = {
        "description": "OK",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}},
    }
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(
            paths,
            components={
                "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
                "schemas": {
                    "AuthResponse": {
                        "type": "object",
                        "properties": {"access_token": {"type": "string"}},
                    }
                },
            },
        )
    )
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.extract_selector == "/access_token"
    assert source.target_scheme == "BearerAuth"


def test_walk_token_via_chained_refs(ctx):
    paths = _build_register_and_login()
    paths["/login"]["post"]["responses"]["200"] = {
        "description": "OK",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}},
    }
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(
            paths,
            components={
                "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
                "schemas": {
                    "AuthResponse": {
                        "type": "object",
                        "properties": {"data": {"$ref": "#/components/schemas/AuthData"}},
                    },
                    "AuthData": {
                        "type": "object",
                        "properties": {"access_token": {"type": "string"}},
                    },
                },
            },
        )
    )
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.extract_selector == "/data/access_token"


def test_resolve_token_source_falls_back_when_login_has_no_security(ctx):
    paths = _login_with_response_token()
    paths["/login"]["post"].pop("security", None)
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **_BEARER_SCHEME))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.target_scheme == "BearerAuth"


def test_resolve_token_source_fallback_prefers_bearer_over_apikey(ctx):
    paths = _login_with_response_token()
    paths["/login"]["post"].pop("security", None)
    schemes = {
        "components": {
            "securitySchemes": {
                "ApiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        }
    }
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths, **schemes))
    register = find_register_candidates(schema)[0]
    login = find_login_for_register(schema, register)
    source = resolve_token_source(schema, login)
    assert source is not None
    assert source.target_scheme == "BearerAuth"


def test_register_skips_admin_paths(ctx):
    paths = {
        "/admin/users": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/admin/users/login": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(paths))
    assert find_register_candidates(schema) == []
