from functools import partial

import pytest
from hypothesis import find

import schemathesis
from schemathesis.models import APIOperation
from schemathesis.specs.openapi.parameters import OpenAPI20Parameter, OpenAPI30Parameter


@pytest.mark.operations("get_user", "update_user")
def test_get_operation_via_remote_reference(openapi_version, schema_url):
    schema = schemathesis.from_uri(schema_url)
    first = schema.get_operation_by_reference(f"{schema_url}#/paths/~1users~1{{user_id}}/patch")
    resolved = schema.get_operation_by_reference(f"{schema_url}#/paths/~1users~1{{user_id}}/patch")
    # Check caching
    assert resolved is first
    assert isinstance(resolved, APIOperation)
    assert resolved.path == "/users/{user_id}"
    assert resolved.method.upper() == "PATCH"
    assert len(resolved.query) == 1
    # Via common parameters for all methods
    if openapi_version.is_openapi_2:
        assert isinstance(resolved.query[0], OpenAPI20Parameter)
        assert resolved.query[0].definition == {"in": "query", "name": "common", "required": True, "type": "integer"}
    if openapi_version.is_openapi_3:
        assert isinstance(resolved.query[0], OpenAPI30Parameter)
        assert resolved.query[0].definition == {
            "in": "query",
            "name": "common",
            "required": True,
            "schema": {"type": "integer"},
        }


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "update_user")
def test_operation_cache_sharing(mocker, schema_url):
    # When the same operation is accessed via different methods
    # The second access should use cache

    def setup_mock(schema, key):
        return mocker.patch.object(schema._operation_cache, key, side_effect=ValueError("Not cached"))

    reference = f"{schema_url}#/paths/~1users~1{{user_id}}/patch"
    operation_id = "updateUser"

    schema = schemathesis.from_uri(schema_url)
    first = schema.get_operation_by_reference(reference)
    # After accessing by reference, there should not be an attempt to insert it again
    setup_mock(schema, "insert_operation")
    second = schema.get_operation_by_id(operation_id)
    assert first is second
    # And the other way around
    schema = schemathesis.from_uri(schema_url)
    first = schema.get_operation_by_id(operation_id)
    second = schema.get_operation_by_reference(reference)
    assert first is second
    # And the cache has just a single entry
    assert len(schema._operation_cache._operations) == 1
    # Direct access should also add an entry to the "by-id" cache
    first = schema["/users/{user_id}"]["GET"]
    # It should be taken from the "by-id" cache
    setup_mock(schema, "get_operation_by_traversal_key")
    second = schema.get_operation_by_id("getUser")
    assert first is second
    assert len(schema._operation_cache._operations) == 2


SINGLE_METHOD_PATHS = {
    "/test-2": {"get": {"responses": {"200": {"description": "OK"}}}},
}
TWO_METHOD_PATHS = {
    "/test": {
        "get": {"responses": {"200": {"description": "OK"}}},
        "post": {"responses": {"200": {"description": "OK"}}},
    },
}


def matches_operation(case, operation):
    return operation.method.upper() == case.method.upper() and operation.full_path == case.full_path


def test_path_as_strategy(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = TWO_METHOD_PATHS
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    operations = schema["/test"]
    strategy = operations.as_strategy()
    for operation in operations.values():
        # All fields should be possible to generate
        find(strategy, partial(matches_operation, operation=operation))


def test_schema_as_strategy(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {**SINGLE_METHOD_PATHS, **TWO_METHOD_PATHS}
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    strategy = schema.as_strategy()
    for operations in schema.values():
        for operation in operations.values():
            # All operations should be possible to generate
            find(strategy, partial(matches_operation, operation=operation))
