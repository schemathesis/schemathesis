from __future__ import annotations

import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Ok


@pytest.mark.parametrize(
    ("path_item", "expected"),
    [
        (
            {
                "servers": [{"url": "https://path.example.com/p"}],
                "get": {
                    "servers": [{"url": "https://op.example.com/o"}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "https://op.example.com/o",
        ),
        (
            {
                "servers": [{"url": "https://path.example.com/p"}],
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "https://path.example.com/p",
        ),
        (
            {
                "servers": [{"url": "https://path.example.com/p"}],
                "get": {"servers": [], "responses": {"200": {"description": "OK"}}},
            },
            "https://path.example.com/p",
        ),
    ],
    ids=["operation-wins", "path-when-no-operation", "empty-operation-falls-through"],
)
def test_precedence(ctx, path_item, expected):
    schema = ctx.openapi.load_schema({"/admin": path_item})
    assert schema["/admin"]["GET"].base_url == expected


def test_empty_path_servers_falls_through_to_global(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/admin": {
                "servers": [],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
        servers=[{"url": "https://global.example.com/g"}],
    )
    assert schema["/admin"]["GET"].base_url == schema.get_base_url()


def test_no_per_scope_servers_uses_global(ctx):
    # Global-only schemas keep today's behavior.
    schema = ctx.openapi.load_schema(
        {
            "/admin": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/public": {"get": {"responses": {"200": {"description": "OK"}}}},
        },
        servers=[{"url": "https://api.example.com/v1"}],
    )
    fallback = schema.get_base_url()
    assert schema["/admin"]["GET"].base_url == fallback
    assert schema["/public"]["GET"].base_url == fallback


def test_variable_substitution_from_defaults(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "servers": [
                        {
                            "url": "https://{host}.example.com/{version}",
                            "variables": {
                                "host": {"default": "api"},
                                "version": {"default": "v3"},
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert schema["/items"]["GET"].base_url == "https://api.example.com/v3"


def test_absolute_url_passes_through(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/admin": {
                "servers": [{"url": "https://other.example.com:8443/admin/v1"}],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    )
    assert schema["/admin"]["GET"].base_url == "https://other.example.com:8443/admin/v1"


def test_relative_url_combines_with_location(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/admin": {
                "servers": [{"url": "/admin/v1"}],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    base_url = schema["/admin"]["GET"].base_url
    assert base_url.startswith("http://127.0.0.1:")
    assert base_url.endswith("/admin/v1")


@pytest.mark.parametrize(
    ("operation_definition", "match"),
    [
        (
            {
                "servers": [{"url": "https://{host}.example.com"}],
                "responses": {"200": {"description": "OK"}},
            },
            "references undefined variable",
        ),
        (
            {
                "servers": [{"not_url": "bogus"}],
                "responses": {"200": {"description": "OK"}},
            },
            "url",
        ),
        (
            {
                "servers": "https://api.example.com",
                "responses": {"200": {"description": "OK"}},
            },
            "servers",
        ),
    ],
    ids=["missing-variable", "missing-url", "servers-not-list"],
)
def test_invalid_servers_raises(ctx, operation_definition, match):
    schema = ctx.openapi.load_schema({"/items": {"get": operation_definition}})
    with pytest.raises(InvalidSchema, match=match):
        schema["/items"]["GET"]


def test_config_base_url_short_circuits(ctx):
    config = SchemathesisConfig.from_dict({"base-url": "https://override.example.com/api"})
    schema_dict = ctx.openapi.build_schema(
        {
            "/admin": {
                "servers": [{"url": "https://path.example.com/p"}],
                "get": {
                    "servers": [{"url": "https://op.example.com/o"}],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
        servers=[{"url": "https://global.example.com/g"}],
    )
    schema = schemathesis.openapi.from_dict(schema_dict, config=config)
    assert schema["/admin"]["GET"].base_url == "https://override.example.com/api"


def _per_op_schema_dict(ctx):
    return ctx.openapi.build_schema(
        {
            "/admin": {
                "get": {
                    "operationId": "adminGet",
                    "servers": [{"url": "https://op.example.com/o"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )


def test_get_all_operations_returns_per_op_base_url(ctx):
    schema = ctx.openapi.from_full_schema(_per_op_schema_dict(ctx))
    operations = [result.ok() for result in schema.get_all_operations() if isinstance(result, Ok)]
    assert [op.base_url for op in operations] == ["https://op.example.com/o"]


def test_find_operation_by_id_returns_per_op_base_url(ctx):
    schema = ctx.openapi.from_full_schema(_per_op_schema_dict(ctx))
    assert schema.find_operation_by_id("adminGet").base_url == "https://op.example.com/o"


def test_find_operation_by_reference_returns_per_op_base_url(ctx):
    # Lazy branch: never iterate so the lookup tables are empty when this fires.
    schema = ctx.openapi.from_full_schema(_per_op_schema_dict(ctx))
    operation = schema.find_operation_by_reference("#/paths/~1admin/get")
    assert operation.base_url == "https://op.example.com/o"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_engine_routes_per_path(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.per_path_servers()
    assert cli.run(api.schema_url, "--max-examples=2") == snapshot_cli
    assert api.calls_under("/zone-a/api/admin"), "Engine did not route /api/admin to its per-path server"
    assert api.calls_under("/zone-b/api/public"), "Engine did not route /api/public to its per-path server"
    assert not api.calls_to("/api/admin") and not api.calls_to("/api/public"), "Engine misrouted to schema paths"
