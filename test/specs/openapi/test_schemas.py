import datetime
import json
import sys
import threading

import pytest
from flask import jsonify

import schemathesis
from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.core.result import Err, Ok
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
        # Repeated slashes
        ("GET", "/users//42"),
        # Missing trailing slash
        ("GET", "/teams/5"),
    ],
)
def test_find_operation_by_path_no_match(ctx, method, path):
    schema = ctx.openapi.load_schema(
        {
            "/users/{user_id}": {
                "get": {"operationId": "get_user", "responses": {"200": {"description": "OK"}}},
                "patch": {"operationId": "update_user", "responses": {"200": {"description": "OK"}}},
            },
            "/teams/{team_id}/": {"get": {"responses": {"200": {"description": "OK"}}}},
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
    operation = ctx.openapi.load_operation(
        "PUT /test",
        {
            "parameters": [{"$ref": "#/components/parameters/idOrUUID"}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.0.0",
        components={
            "parameters": {
                "idOrUUID": {"in": {}}  # Invalid: should be string like "path", "query", etc.
            }
        },
    )
    # Should not raise TypeError: unhashable type: 'dict'
    assert list(operation.iter_parameters()) == []


# The Open API spec allows any "-suffix" on the version string.
SUFFIXED_SPEC_VERSIONS = ["3.1.0-custom", "3.1.0-rc1", "3.0.0-2024.1", "3.0.3-alpha.7"]


@pytest.mark.parametrize("spec_version", SUFFIXED_SPEC_VERSIONS)
def test_spec_version_with_suffix(ctx, spec_version):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}, version=spec_version
    )
    schema.validate()
    assert schema.specification.version == spec_version
    assert schema["/users"]["GET"].label == "GET /users"


def test_validate_skipped_for_non_json_values(ctx):
    raw_schema = ctx.openapi.build_schema({"/a": {"get": {"responses": []}}})
    raw_schema["info"]["version"] = datetime.date(2020, 1, 1)
    schema = schemathesis.openapi.from_dict(raw_schema)
    assert schema.validate() is None


@pytest.mark.parametrize("spec_version", ["3.1.0-custom", "3.1.0-rc1"])
def test_spec_version_with_suffix_and_no_paths(ctx, spec_version):
    assert list(ctx.openapi.load_schema(None, version=spec_version).get_all_operations()) == []


@pytest.mark.parametrize("extension", [None, [], "note", 42])
def test_non_object_response_vendor_extension(ctx, extension):
    # A `x-` key inside `responses` may hold any JSON value, not just an object.
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "operationId": "get_users",
                    "responses": {
                        "x-note": extension,
                        "200": {
                            "description": "OK",
                            "links": {"Self": {"operationId": "get_users"}},
                        },
                    },
                }
            }
        }
    )
    assert (schema.statistic.operations.total, schema.statistic.transitions.total) == (1, 1)
    operation = schema["/users"]["GET"]
    assert [(status_code, dict(response.iter_links())) for status_code, response in operation.responses.items()] == [
        ("200", {"Self": {"operationId": "get_users"}})
    ]


@pytest.mark.parametrize("version", ["2.0", "3.0.2", "3.1.0"])
@pytest.mark.parametrize(
    "extension",
    [
        "/apis/registry/v2",
        None,
        [],
        42,
        {"note": "text"},
        {"get": {"operationId": "hidden", "responses": {"200": {"description": "OK"}}}},
    ],
)
def test_paths_object_vendor_extension(ctx, version, extension):
    # The Paths Object is extensible, so an `x-` key beside the path templates is not an operation.
    schema = ctx.openapi.load_schema(
        {
            "x-codegen-contextRoot": extension,
            "/things": {"get": {"operationId": "get_things", "responses": {"200": {"description": "OK"}}}},
        },
        version=version,
    )
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (1, 1)
    assert [
        result.ok().label if isinstance(result, Ok) else repr(result.err()) for result in schema.get_all_operations()
    ] == ["GET /things"]
    assert schema.find_operation_by_path("GET", "/things").label == "GET /things"


@pytest.mark.parametrize("path_item", ["invalid", None, 42, []])
def test_non_object_path_item(ctx, path_item):
    schema = ctx.openapi.load_schema(
        {
            "/broken": path_item,
            "/things": {"get": {"operationId": "get_things", "responses": {"200": {"description": "OK"}}}},
        }
    )
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (1, 1)
    results = list(schema.get_all_operations())
    assert [(result.err().path, result.err().method) for result in results if isinstance(result, Err)] == [
        ("/broken", None)
    ]
    assert [result.ok().label for result in results if isinstance(result, Ok)] == ["GET /things"]
    assert schema.find_operation_by_path("GET", "/things").label == "GET /things"


SELF_DOCUMENTED_PATHS = {
    "/openapi.json": {"get": {"responses": {"200": {"description": "OK"}}}},
    "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
}


def test_schema_endpoint_is_not_tested(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(SELF_DOCUMENTED_PATHS)
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    assert [result.ok().label for result in schema.get_all_operations()] == ["GET /users"]
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (1, 1)


def test_schema_endpoint_is_tested_when_selected(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(SELF_DOCUMENTED_PATHS)
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app)).include(path="/openapi.json")
    assert [result.ok().label for result in schema.get_all_operations()] == ["GET /openapi.json"]
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (2, 1)


def test_schema_endpoint_is_not_tested_for_a_relative_wsgi_path(ctx):
    app, _ = ctx.openapi.make_flask_app(SELF_DOCUMENTED_PATHS)
    schema = schemathesis.openapi.from_wsgi("openapi.json", app)
    assert [result.ok().label for result in schema.get_all_operations()] == ["GET /users"]


def test_schema_endpoint_is_not_found_by_path(ctx):
    app, _ = ctx.openapi.make_flask_app(SELF_DOCUMENTED_PATHS)
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)
    assert schema.find_operation_by_path("GET", "/openapi.json") is None
    assert schema.find_operation_by_path("GET", "/users").label == "GET /users"


def test_filter_excluding_the_schema_endpoint_is_not_reported_as_unmatched(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(SELF_DOCUMENTED_PATHS)
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app)).exclude(path="/openapi.json")
    assert schema.statistic.unmatched_filters == []


def test_non_get_operation_on_schema_path_is_tested(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/openapi.json": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    assert [result.ok().label for result in schema.get_all_operations()] == ["POST /openapi.json"]


SELF_DOCUMENTED_UNDER_BASE_PATH = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
    "servers": [{"url": "/api/v1"}],
    "paths": {
        "/schema/": {"get": {"responses": {"200": {"description": "OK"}}}},
        "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
    },
}


@pytest.mark.parametrize(
    "fetch_path",
    ["/api/v1/schema/", "/api/v1/schema/?format=json", "/api/v1/schema"],
    ids=["exact", "query-string", "no-trailing-slash"],
)
def test_schema_endpoint_under_base_path_is_not_tested(ctx, app_runner, fetch_path):
    app = ctx.openapi.make_flask_app_from_schema(SELF_DOCUMENTED_UNDER_BASE_PATH)

    @app.route("/api/v1/schema/")
    def schema_document():
        return jsonify(SELF_DOCUMENTED_UNDER_BASE_PATH)

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app, path=fetch_path))
    assert [result.ok().label for result in schema.get_all_operations()] == ["GET /users"]


def test_path_with_lone_surrogate_is_a_schema_error(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/a\ud800b": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (2, 2)
    results = list(schema.get_all_operations())
    assert [(result.err().path, result.err().method) for result in results if isinstance(result, Err)] == [
        ("/a\ud800b", None)
    ]
    assert [result.ok().label for result in results if isinstance(result, Ok)] == ["GET /users"]


def test_schema_loaded_from_file_keeps_every_operation(ctx, tmp_path):
    location = tmp_path / "openapi.json"
    spec = ctx.openapi.build_schema({str(location): {"get": {"responses": {"200": {"description": "OK"}}}}})
    location.write_text(json.dumps(spec))
    schema = schemathesis.openapi.from_path(location)
    assert [result.ok().label for result in schema.get_all_operations()] == [f"GET {location}"]


def test_percent_encoded_base_path_reserved_char_survives_in_full_path(ctx):
    # Decoding a "%3F" the schema author wrote into a static `basePath` turns it into a query delimiter.
    operation = ctx.openapi.load_operation(
        "GET /movies.csv&imdb_id={IMDBid}",
        {"responses": {"200": {"description": "OK"}}},
        version="2.0",
        basePath="/api-v2/%3Fsource=http:/example.com/api-v2",
    )
    assert operation.full_path == "/api-v2/%3Fsource=http:/example.com/api-v2/movies.csv&imdb_id={IMDBid}"


def load_users_schema(ctx):
    user_response = {"$ref": "#/components/responses/User"}
    return ctx.openapi.load_schema(
        {
            "/users": {"post": {"operationId": "createUser", "responses": {"201": user_response}}},
            "/admins": {"post": {"operationId": "createAdmin", "responses": {"201": user_response}}},
            "/users/{userId}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": user_response},
                }
            },
        },
        components={
            "schemas": {"User": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
            "responses": {
                "User": {
                    "description": "User",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                }
            },
        },
    )


def test_every_lookup_returns_the_same_operation(ctx):
    schema = load_users_schema(ctx)
    operation = next(result.ok() for result in schema.get_all_operations() if result.ok().label == "POST /users")

    assert [
        found is operation
        for found in (
            next(result.ok() for result in schema.get_all_operations() if result.ok().label == "POST /users"),
            schema["/users"]["POST"],
            schema.find_operation_by_label("POST /users"),
            schema.find_operation_by_id("createUser"),
            schema.find_operation_by_reference("#/paths/~1users/post"),
        )
    ] == [True] * 5


def test_before_init_operation_runs_once_per_operation(ctx):
    schema = load_users_schema(ctx)
    initialized = []

    @schema.hook
    def before_init_operation(context, operation):
        initialized.append(operation.label)

    list(schema.get_all_operations())
    list(schema.get_all_operations())
    schema["/users"]["POST"]
    schema.find_operation_by_id("getUser")

    assert sorted(initialized) == ["GET /users/{userId}", "POST /admins", "POST /users"]


def test_links_injected_after_operations_were_built_are_visible(ctx):
    def injected_links(prebuild):
        schema = load_users_schema(ctx)
        if prebuild:
            list(schema.get_all_operations())
        schema.analysis.inject_links()
        return {
            label: schema.find_operation_by_label(label).responses.get(status).definition.get("links", {})
            for label, status in (("POST /users", "201"), ("POST /admins", "201"), ("GET /users/{userId}", "200"))
        }

    expected = injected_links(prebuild=False)

    assert expected["POST /users"]
    assert injected_links(prebuild=True) == expected


def test_links_injected_through_a_clone_are_visible_in_other_clones(ctx):
    expected = load_users_schema(ctx)
    expected.analysis.inject_links()
    schema = load_users_schema(ctx)
    first, second = schema.clone(), schema.clone()
    list(first.get_all_operations())

    second.analysis.inject_links()

    assert first["/users"]["POST"].responses.get("201").definition.get("links") == expected["/users"][
        "POST"
    ].responses.get("201").definition.get("links")


def test_concurrent_lookups_build_one_operation(ctx):
    schema = load_users_schema(ctx)
    initialized = []

    @schema.hook
    def before_init_operation(context, operation):
        initialized.append(operation.label)

    barrier = threading.Barrier(16)
    found = []

    def lookup():
        barrier.wait()
        found.append(schema["/users"]["POST"])

    # Switching threads often makes the race between first users likely.
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    threads = [threading.Thread(target=lookup) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    sys.setswitchinterval(previous)

    assert (len({id(operation) for operation in found}), initialized) == (1, ["POST /users"])


def test_cached_lookups_do_not_parse_parameters_again(ctx):
    schema = load_users_schema(ctx)
    operation = schema["/users/{userId}"]["GET"]
    # Only a re-parse would notice this edit, which bypasses cache invalidation.
    schema.raw_schema["paths"]["/users/{userId}"]["get"]["parameters"] = [{"$ref": "#/components/parameters/Missing"}]

    assert [
        found is operation
        for found in (
            next(result.ok() for result in schema.get_all_operations() if result.ok().label == "GET /users/{userId}"),
            schema["/users/{userId}"]["GET"],
            schema.find_operation_by_id("getUser"),
            schema.find_operation_by_reference("#/paths/~1users~1{userId}/get"),
        )
    ] == [True] * 4
