from __future__ import annotations

import json

import pytest

import schemathesis
from schemathesis.core.jsonschema.resolver import make_root_resolver
from schemathesis.specs.openapi.stateful.dependencies import analyze, inject_links
from schemathesis.specs.openapi.stateful.dependencies.models import extract_nested_fk_fields


def test_extract_nested_fk_fields_with_direct_resolver():
    root_schema = {
        "components": {
            "schemas": {
                "Shipping": {
                    "type": "object",
                    "properties": {
                        "location": {"$ref": "#/components/schemas/Location"},
                    },
                },
                "Location": {
                    "type": "object",
                    "properties": {
                        "warehouse_id": {"type": "string"},
                        "address": {"type": "string"},
                    },
                },
            }
        }
    }
    schema = {
        "type": "object",
        "properties": {
            "shipping": {"$ref": "#/components/schemas/Shipping"},
        },
    }

    nested_fk_fields = extract_nested_fk_fields(schema, make_root_resolver(root_schema))

    assert len(nested_fk_fields) == 1
    field = nested_fk_fields[0]
    assert field.pointer == "/shipping/location/warehouse_id"
    assert field.field_name == "warehouse_id"
    assert field.target_resource == "Warehouse"
    assert field.target_field == "id"
    assert field.is_array is False


def _json_response(status, schema):
    return {status: {"description": "OK", "content": {"application/json": {"schema": schema}}}}


def _path_param(name):
    return [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}}]


def _inferred_links(graph):
    return [
        [entry.producer_operation_ref, entry.status_code, definition.to_openapi()]
        for entry in graph.iter_links()
        for definition in entry.links.values()
    ]


# POST /orders response lacks `shipping`, so only GET /orders/{orderId} feeds /warehouses/{id}
def test_no_nested_fk_link_from_response_lacking_parent_field(ctx):
    order_full = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "shipping": {"type": "object", "properties": {"warehouse_id": {"type": "string"}}},
        },
    }
    order_short = {"type": "object", "properties": {"id": {"type": "string"}, "total": {"type": "number"}}}
    warehouse = {"type": "object", "properties": {"id": {"type": "string"}}}
    schema = ctx.openapi.load_schema(
        {
            "/orders/{orderId}": {
                "get": {
                    "operationId": "getOrder",
                    "parameters": _path_param("orderId"),
                    "responses": _json_response("200", order_full),
                }
            },
            "/orders": {"post": {"operationId": "createOrder", "responses": _json_response("201", order_short)}},
            "/warehouses/{id}": {
                "get": {
                    "operationId": "getWarehouse",
                    "parameters": _path_param("id"),
                    "responses": _json_response("200", warehouse),
                }
            },
        }
    )
    assert _inferred_links(analyze(schema)) == [
        [
            "#/paths/~1orders/post",
            "201",
            {
                "operationRef": "#/paths/~1orders~1{orderId}/get",
                "parameters": {"path.orderId": "$response.body#/id"},
                "x-schemathesis": {"is_inferred": True},
            },
        ],
        [
            "#/paths/~1orders~1{orderId}/get",
            "200",
            {
                "operationRef": "#/paths/~1warehouses~1{id}/get",
                "parameters": {"path.id": "$response.body#/shipping/warehouse_id"},
                "x-schemathesis": {"is_inferred": True},
            },
        ],
    ]


def test_named_scalar_not_bound_when_operation_produces_the_resource(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/countries": {
                "get": {
                    "operationId": "listCountries",
                    "responses": _json_response("200", {"type": "array", "items": {"type": "string"}}),
                },
                "put": {
                    "operationId": "replaceCountries",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"country": {"type": "string"}},
                                    "required": ["country"],
                                }
                            }
                        }
                    },
                    "responses": _json_response("201", {"type": "string"}),
                },
            }
        }
    )
    assert _inferred_links(analyze(schema)) == []


def test_inject_links_suffixes_name_past_existing_collisions(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                            "links": {
                                "GetUser": {"operationId": "listUsers"},
                                "GetUser_0": {"operationId": "listUsers"},
                            },
                        }
                    },
                },
                "get": {"operationId": "listUsers", "responses": {"200": {"description": "OK"}}},
            },
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": _path_param("id"),
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert inject_links(schema) == 1
    assert schema.raw_schema["paths"]["/users"]["post"]["responses"]["201"]["links"] == {
        "GetUser": {"operationId": "listUsers"},
        "GetUser_0": {"operationId": "listUsers"},
        "GetUser_1": {
            "operationRef": "#/paths/~1users~1{id}/get",
            "parameters": {"path.id": "$response.body#/id"},
            "x-schemathesis": {"is_inferred": True},
        },
    }


_LIST_CONFIGS = {
    "description": "OK",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"broker_id": {"type": "integer"}, "name": {"type": "string"}},
                            "required": ["name"],
                        },
                    }
                },
            }
        }
    },
    "links": {"ListAllConfigs": {"operationRef": "#/paths/~1brokers~1-~1configs/get"}},
}


# `/brokers/-/configs` shares its response with `/brokers/{broker_id}/configs` but has no `{broker_id}` to forward.
@pytest.mark.parametrize(
    ("owner_response", "shared_response", "components"),
    [
        (
            {"$ref": "#/components/responses/ListConfigs"},
            {"$ref": "#/components/responses/ListConfigs"},
            {"responses": {"ListConfigs": _LIST_CONFIGS}},
        ),
        (_LIST_CONFIGS, {"$ref": "#/paths/~1brokers~1{broker_id}~1configs/get/responses/200"}, {}),
    ],
    ids=["component", "inline"],
)
def test_inject_links_keeps_links_off_operations_sharing_a_response(ctx, owner_response, shared_response, components):
    schema = ctx.openapi.load_schema(
        {
            "/brokers/{broker_id}/configs": {
                "get": {"parameters": _path_param("broker_id"), "responses": {"200": owner_response}}
            },
            "/brokers/-/configs": {"get": {"responses": {"200": shared_response}}},
            # Unparsable responses are left alone
            "/brokers": {"get": {"responses": {"200": {"$ref": "#/components/responses/Missing"}}}},
            "/brokers/archived": {"get": {"responses": []}},
            "/brokers/{broker_id}/configs/{name}": {
                "get": {
                    "parameters": _path_param("broker_id") + _path_param("name"),
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components=components,
    )
    inject_links(schema)

    paths = schema.raw_schema["paths"]
    get_config = "#/paths/~1brokers~1{broker_id}~1configs~1{name}/get"
    list_all = {"operationRef": "#/paths/~1brokers~1-~1configs/get"}
    assert paths["/brokers/{broker_id}/configs"]["get"]["responses"]["200"]["links"] == {
        "ListAllConfigs": list_all,
        "GetConfig": {
            "operationRef": get_config,
            "x-schemathesis": {"is_inferred": True},
            "parameters": {"path.name": "$response.body#/data/*/name", "path.broker_id": "$request.path.broker_id"},
        },
    }
    assert paths["/brokers/-/configs"]["get"]["responses"]["200"]["links"] == {
        "ListAllConfigs": list_all,
        "GetConfig": {
            "operationRef": get_config,
            "x-schemathesis": {"is_inferred": True},
            "parameters": {"path.name": "$response.body#/data/*/name"},
        },
    }


# A response living in the root document keeps resolving there when its operations come from another file.
def test_inject_links_keeps_response_references_of_external_operations(tmp_path):
    config = {"$ref": "#/components/schemas/Config"}
    (tmp_path / "ops.json").write_text(
        json.dumps(
            {
                path: {"get": {"responses": {"200": {"$ref": "root.json#/components/responses/ListConfigs"}}}}
                for path in ("configs", "archived")
            }
        )
    )
    (tmp_path / "root.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.2",
                "info": {"title": "Test", "version": "0.1"},
                "paths": {
                    "/configs": {"$ref": "ops.json#/configs"},
                    "/archived-configs": {"$ref": "ops.json#/archived"},
                    "/configs/{name}": {
                        "get": {
                            "parameters": _path_param("name"),
                            "responses": _json_response("200", config),
                        }
                    },
                },
                "components": {
                    "responses": {
                        "ListConfigs": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "array", "items": config}}},
                        }
                    },
                    "schemas": {
                        "Config": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        }
                    },
                },
            }
        )
    )
    schema = schemathesis.openapi.from_path(str(tmp_path / "root.json"))
    inject_links(schema)

    for path in ("/configs", "/archived-configs"):
        assert schema[path]["GET"].responses.get("200").get_schema().unresolvable_reference is None, path
