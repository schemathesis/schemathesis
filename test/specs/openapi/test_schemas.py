import pytest

import schemathesis
from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.schemas import APIOperation


def test_get_operation_via_remote_reference(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    resolved = schema.find_operation_by_reference(f"{api.schema_url}#/paths/~1users~1{{user_id}}/patch")
    assert isinstance(resolved, APIOperation)
    assert resolved.path == "/users/{user_id}"
    assert resolved.method.upper() == "PATCH"
    assert len(resolved.query) == 1
    # Via common parameters for all methods
    assert resolved.query[0].definition == {
        "in": "query",
        "name": "common",
        "required": True,
        "schema": {"type": "integer"},
    }


@pytest.mark.parametrize(
    ("method", "path", "expected_path", "expected_method", "expected_operation_id"),
    [
        # Match with path parameters
        ("GET", "/users/42", "/users/{user_id}", "get", "get_user"),
        # Match different method on same path
        ("PATCH", "/users/42", "/users/{user_id}", "patch", "update_user"),
        # Match nested path with multiple parameters
        ("GET", "/users/42/posts/123", "/users/{user_id}/posts/{post_id}", "get", "get_post"),
    ],
)
def test_find_operation_by_path_match(ctx, method, path, expected_path, expected_method, expected_operation_id):
    schema = ctx.openapi.load_schema(
        {
            "/users/{user_id}": {
                "get": {"operationId": "get_user", "responses": {"200": {"description": "OK"}}},
                "patch": {"operationId": "update_user", "responses": {"200": {"description": "OK"}}},
            },
            "/users/{user_id}/posts/{post_id}": {
                "get": {"operationId": "get_post", "responses": {"200": {"description": "OK"}}}
            },
        },
        version="3.0.0",
    )

    operation = schema.find_operation_by_path(method, path)
    assert (operation.path, operation.method, operation.definition.raw["operationId"]) == (
        expected_path,
        expected_method,
        expected_operation_id,
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # Wrong method
        ("DELETE", "/users/42"),
        # Wrong path
        ("GET", "/nonexistent"),
    ],
)
def test_find_operation_by_path_no_match(ctx, method, path):
    schema = ctx.openapi.load_schema(
        {
            "/users/{user_id}": {
                "get": {"operationId": "get_user", "responses": {"200": {"description": "OK"}}},
                "patch": {"operationId": "update_user", "responses": {"200": {"description": "OK"}}},
            },
        },
        version="3.0.0",
    )

    operation = schema.find_operation_by_path(method, path)
    assert operation is None


@pytest.mark.parametrize(
    ("paths", "reference", "expected"),
    [
        (
            {
                "/users": {
                    "parameters": [{"in": "query", "name": "common", "required": True}],
                    "get": {"operationId": "get_users", "responses": {"200": {"description": "OK"}}},
                },
                "x-codegen-contextRoot": "/apis/registry/v2",
            },
            "#/paths/~1users/get",
            "/users",
        ),
        (
            {
                "/users": {
                    "parameters": [{"in": "query", "name": "common", "required": True}],
                    "get": {"operationId": "get_users", "responses": {"200": {"description": "OK"}}},
                },
                "/alias": {"$ref": "#/paths/~1vendor"},
                "/vendor": "invalid",
            },
            "#/paths/~1users/get",
            "/users",
        ),
    ],
)
def test_operation_lookup_ignores_invalid_entries(ctx, paths, reference, expected):
    schema = ctx.openapi.load_schema(paths)
    schema.as_state_machine()
    assert schema.find_operation_by_reference(reference).path == expected


@pytest.mark.parametrize(
    "paths",
    [
        {
            "/alias": "invalid",
            "/users": {"get": {"operationId": "get_users", "responses": {"200": {"description": "OK"}}}},
        },
        {
            "/alias": {"$ref": "#/paths/~1vendor"},
            "/vendor": "invalid",
            "/users": {"get": {"operationId": "get_users", "responses": {"200": {"description": "OK"}}}},
        },
    ],
)
def test_operation_lookup_non_mapping_shared_params(ctx, paths):
    schema = ctx.openapi.load_schema(paths)
    with pytest.raises(OperationNotFound):
        schema.find_operation_by_reference("#/paths/~1alias/get")


def test_query_method_operation_is_discovered(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/search": {
                "query": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.2.0",
    )
    operations = list(schema.get_all_operations())
    assert len(operations) == 1
    assert operations[0].ok().method == "query"


def test_external_file_ref_in_body_raises_invalid_schema(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/probe": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "external.json"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    with pytest.raises(InvalidSchema, match="Unresolvable"):
        schema["/probe"]["POST"]


def test_non_string_parameter_location(ctx):
    # When a parameter has an invalid non-string `in` value (e.g., empty dict),
    # schema loading should skip the invalid parameter without crashing
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "put": {
                    "parameters": [{"$ref": "#/components/parameters/idOrUUID"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
        components={
            "parameters": {
                "idOrUUID": {"in": {}}  # Invalid: should be string like "path", "query", etc.
            }
        },
    )
    operation = schema["/test"]["PUT"]
    # Should not raise TypeError: unhashable type: 'dict'
    assert list(operation.iter_parameters()) == []


def test_referenced_parameters_share_one_bundle_cache_entry(ctx):
    # The bundle cache must key on the definition a parameter refers to, not on the
    # identity of the dict that refers to it. Both operations below cite the same
    # parameter, but each citation is a separate dict, so an `id()`-keyed cache stores
    # two entries for one definition.
    #
    # That key is also unsound: it outlives the dict it names, and once the dict is
    # freed CPython can reuse its address for an unrelated parameter, which then gets
    # the wrong definition back -- silently dropping a path parameter and reporting a
    # valid spec as invalid.
    schema = ctx.openapi.load_schema(
        {
            "/alpha/{item_id}": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/ItemId"}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/beta/{item_id}": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/ItemId"}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        version="3.0.0",
        components={
            "parameters": {"ItemId": {"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}}
        },
    )
    list(schema.get_all_operations())

    assert len(schema._bundle_cache) == 1
