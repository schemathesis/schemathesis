from __future__ import annotations

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
