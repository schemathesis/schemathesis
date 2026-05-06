from __future__ import annotations

import pytest
import requests
from hypothesis import given, settings

from schemathesis.config import GenerationConfig
from schemathesis.core import deserialization
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation.modes import GenerationMode
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor
from schemathesis.resources.repository import (
    MAX_CONTEXTS_PER_TYPE,
    PER_CONTEXT_CAPACITY,
    ResourceRepository,
)
from schemathesis.specs.openapi.extra_data_source import ParameterRequirement
from schemathesis.specs.openapi.negative import GeneratedValue

USER_RESOURCE = "User"
POST_USERS = "POST /users"
CREATED = 201


@pytest.fixture
def user_schema_builder(ctx):
    def build(*, status_code="201", response_schema=None, extra_endpoints=None):
        if response_schema is None:
            response_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}

        paths = {
            "/users": {
                "post": {"responses": {status_code: {"content": {"application/json": {"schema": response_schema}}}}}
            },
            # GET /users/{id} consumes User.id so the descriptor stays reachable under
            # the orphan-resource filter; record_response can populate the User pool.
            "/users/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
        if extra_endpoints:
            paths.update(extra_endpoints)

        return ctx.openapi.load_schema(paths)

    return build


@pytest.fixture
def user_schema(user_schema_builder):
    return user_schema_builder()


@pytest.fixture
def user_data_source(user_schema):
    return user_schema.create_extra_data_source()


def test_store_single_resource(user_data_source):
    user_data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "123", "name": "Jane"}
    )
    resources = list(user_data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "123"


def test_ignore_non_matching_status_code(user_data_source):
    # Non-2xx responses should be ignored even though the schema expects 2xx
    user_data_source.repository.record_response(operation=POST_USERS, status_code=404, payload={"id": "1"})
    assert list(user_data_source.repository.iter_instances(USER_RESOURCE)) == []


def test_lenient_2xx_matching(user_data_source):
    # Schema expects 201 but server returns 200 - should still record (both are 2xx)
    user_data_source.repository.record_response(operation=POST_USERS, status_code=200, payload={"id": "1"})
    resources = list(user_data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "1"


def test_many_cardinality_extracts_each_item(user_schema_builder):
    array_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            }
        },
    }
    schema = user_schema_builder(response_schema=array_schema)
    data_source = schema.create_extra_data_source()

    payload = {"items": [{"id": "a"}, {"id": "b"}]}
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert {instance.data["id"] for instance in resources} == {"a", "b"}


def test_wildcard_pointer_unresolvable_before_wildcard_yields_no_entries():
    # Wildcard descriptor pointer where the literal prefix is missing in the payload.
    # `_extract_payload` short-circuits to an empty result without reaching fan-out.
    repository = ResourceRepository(
        [
            ResourceDescriptor(
                resource_name="Item",
                operation="GET /items",
                status_code="200",
                pointer="/missing/*/id",
                cardinality=Cardinality.MANY,
            )
        ]
    )
    repository.record_response(operation="GET /items", status_code=200, payload={"data": [{"id": "a"}]})
    assert repository.iter_instances("Item") == ()


@pytest.mark.parametrize(
    ("paths", "payload", "operation_label", "resource_name", "id_field", "expected_ids"),
    [
        pytest.param(
            {
                "/volumes": {
                    "get": {
                        "operationId": "listVolumes",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "Volumes": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "Name": {"type": "string"},
                                                            "Driver": {"type": "string"},
                                                        },
                                                        "required": ["Name", "Driver"],
                                                    },
                                                },
                                                "Warnings": {"type": "array", "items": {"type": "string"}},
                                            },
                                            "required": ["Volumes"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/volumes/{Name}": {
                    "get": {
                        "operationId": "getVolume",
                        "parameters": [{"name": "Name", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                    }
                },
            },
            {"Volumes": [{"Name": "v1", "Driver": "local"}, {"Name": "v2", "Driver": "local"}], "Warnings": []},
            "GET /volumes",
            "Volume",
            "Name",
            {"v1", "v2"},
            id="multi-array-root-docker-volumes",
        ),
        pytest.param(
            {
                "/sources/{sourceId}/fields": {
                    "get": {
                        "operationId": "listFields",
                        "parameters": [
                            {"name": "sourceId", "in": "path", "required": True, "schema": {"type": "string"}}
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "source_fields": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "label": {"type": "string"},
                                                        },
                                                        "required": ["id"],
                                                    },
                                                },
                                                "total": {"type": "integer"},
                                            },
                                            "required": ["source_fields"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/sources/{sourceId}/fields/{id}": {
                    "get": {
                        "operationId": "getField",
                        "parameters": [
                            {"name": "sourceId", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        ],
                        "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                    }
                },
            },
            {"source_fields": [{"id": "f1", "label": "L1"}, {"id": "f2", "label": "L2"}], "total": 2},
            "GET /sources/{sourceId}/fields",
            "Field",
            "id",
            {"f1", "f2"},
            id="snake-case-wrapper-with-total-sibling",
        ),
        pytest.param(
            {
                "/services/{serviceId}/compliance": {
                    "get": {
                        "operationId": "listCompliance",
                        "parameters": [
                            {"name": "serviceId", "in": "path", "required": True, "schema": {"type": "string"}}
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "compliance": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "status": {"type": "string"},
                                                        },
                                                        "required": ["id"],
                                                    },
                                                },
                                                "count": {"type": "integer"},
                                            },
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/services/{serviceId}/compliance/{id}": {
                    "get": {
                        "operationId": "getCompliance",
                        "parameters": [
                            {"name": "serviceId", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        ],
                        "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                    }
                },
            },
            {"compliance": [{"id": "C1", "status": "ok"}, {"id": "C2", "status": "ok"}], "count": 2},
            "GET /services/{serviceId}/compliance",
            "Compliance",
            "id",
            {"C1", "C2"},
            id="singular-wrapper-noun-array",
        ),
        pytest.param(
            {
                "/products": {
                    "get": {
                        "operationId": "listProducts",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "records": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                        },
                                                        "required": ["id"],
                                                    },
                                                },
                                                "totalSize": {"type": "integer"},
                                                "done": {"type": "boolean"},
                                            },
                                            "required": ["records", "totalSize", "done"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/products/{id}": {
                    "get": {
                        "operationId": "getProduct",
                        "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                    }
                },
            },
            {"records": [{"id": "p1", "name": "n1"}, {"id": "p2", "name": "n2"}], "totalSize": 2, "done": True},
            "GET /products",
            "Product",
            "id",
            {"p1", "p2"},
            id="generic-wrapper-word-salesforce",
        ),
    ],
)
def test_pool_captures_individuals_from_get_list_envelope(
    ctx, paths, payload, operation_label, resource_name, id_field, expected_ids
):
    schema = ctx.openapi.load_schema(paths)
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(operation=operation_label, status_code=200, payload=payload)
    resources = list(data_source.repository.iter_instances(resource_name))
    assert {instance.data.get(id_field) for instance in resources} == expected_ids


def test_pool_captures_individuals_from_map_by_id_response(ctx):
    # Map-by-id payload: keys ARE the identifiers, values are the resources.
    schema = ctx.openapi.load_schema(
        {
            "/teams/statuses": {
                "get": {
                    "operationId": "listTeamStatuses",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": {
                                            "type": "object",
                                            "properties": {"qual_average": {"type": "number"}},
                                        },
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/teams/{teamId}": {
                "get": {
                    "operationId": "getTeam",
                    "parameters": [{"name": "teamId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation="GET /teams/statuses",
        status_code=200,
        payload={"frc1": {"qual_average": 95.0}, "frc2": {"qual_average": 87.0}},
    )
    resources = list(data_source.repository.iter_instances("Team"))
    assert {instance.data.get("teamId") for instance in resources} == {"frc1", "frc2"}


def test_pool_captures_individuals_from_nested_envelope_response(ctx):
    # Spring-style `{response: {content: [...], pageNumber, pageSize}, status, time}` envelope.
    schema = ctx.openapi.load_schema(
        {
            "/flights": {
                "get": {
                    "operationId": "listFlights",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CustomResponseFlightPage"}
                                }
                            }
                        }
                    },
                }
            },
            "/flights/{id}": {
                "get": {
                    "operationId": "getFlight",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        },
        components={
            "schemas": {
                "Flight": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    "required": ["id"],
                },
                "FlightPage": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "array", "items": {"$ref": "#/components/schemas/Flight"}},
                        "pageNumber": {"type": "integer"},
                        "pageSize": {"type": "integer"},
                    },
                },
                "CustomResponseFlightPage": {
                    "type": "object",
                    "properties": {
                        "response": {"$ref": "#/components/schemas/FlightPage"},
                        "status": {"type": "string"},
                        "time": {"type": "string"},
                    },
                },
            }
        },
    )
    data_source = schema.create_extra_data_source()
    payload = {
        "response": {
            "content": [{"id": "f1", "name": "n1"}, {"id": "f2", "name": "n2"}],
            "pageNumber": 0,
            "pageSize": 20,
        },
        "status": "200 OK",
        "time": "2026-04-30T00:00:00Z",
    }
    data_source.repository.record_response(operation="GET /flights", status_code=200, payload=payload)
    resources = list(data_source.repository.iter_instances("Flight"))
    assert {instance.data.get("id") for instance in resources} == {"f1", "f2"}


def test_pool_map_by_id_with_single_segment_path(ctx):
    # Path has one segment; the helper falls back to `from_path(path)` directly.
    schema = ctx.openapi.load_schema(
        {
            "/widgets": {
                "get": {
                    "operationId": "listWidgetMap",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": {
                                            "type": "object",
                                            "properties": {"label": {"type": "string"}},
                                        },
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/widgets/{widgetId}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [{"name": "widgetId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation="GET /widgets",
        status_code=200,
        payload={"w1": {"label": "L1"}, "w2": {"label": "L2"}},
    )
    resources = list(data_source.repository.iter_instances("Widget"))
    assert {instance.data.get("widgetId") for instance in resources} == {"w1", "w2"}


def test_pool_map_by_id_unrecoverable_path_emits_no_descriptor(ctx):
    # Path resolves to no resource name (only path-param segments); helper returns None.
    schema = ctx.openapi.load_schema(
        {
            "/{slug}": {
                "get": {
                    "operationId": "rootMap",
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": {
                                            "type": "object",
                                            "properties": {"value": {"type": "string"}},
                                        },
                                    }
                                }
                            }
                        }
                    },
                }
            }
        }
    )
    descriptors = [d for d in schema.analysis.resource_descriptors if d.operation == "GET /{slug}"]
    assert descriptors == []


def test_orphan_resource_descriptors_are_filtered(ctx):
    # Producer creates a Widget; no operation consumes Widget. The descriptor would only ever
    # write into a bucket nothing reads, so it must not be built.
    schema = ctx.openapi.load_schema(
        {
            "/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            }
        }
    )
    assert [d.resource_name for d in schema.analysis.resource_descriptors] == []


def test_descriptor_kept_when_consumer_exists(ctx):
    # Sibling regression guard: with a consumer present, the descriptor must still be built.
    schema = ctx.openapi.load_schema(
        {
            "/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/widgets/{id}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert [d.resource_name for d in schema.analysis.resource_descriptors] == ["Widget"]


def test_captured_variants_filter_values_invalid_for_destination(ctx):
    # Producer accepts `id: 0` but consumer's path requires `minimum: 1`; pool injection must filter.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/items/{itemId}/sync": {
                "post": {
                    "operationId": "syncItem",
                    "parameters": [
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": 0})
    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": 5})

    sync_op = schema["/items/{itemId}/sync"]["POST"]
    variants = data_source.get_captured_variants(
        operation=sync_op, location=ParameterLocation.PATH, schema=sync_op.path_parameters.schema
    )

    assert variants == [{"itemId": 5}]


def test_data_source_provides_captured_variants(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    get_endpoint = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=get_endpoint)
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "1", "name": "Alice"}
    )
    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "2", "name": "Bob"}
    )

    get_operation = schema["/users/{user_id}"]["GET"]
    path_params_schema = get_operation.path_parameters.schema

    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_params_schema
    )

    assert variants is not None
    assert len(variants) == 2
    assert {"user_id": "1"} in variants
    assert {"user_id": "2"} in variants


def test_record_successful_delete_evicts_pool_entry_and_filters_subsequent_draws(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/items/{itemId}": {
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "Deleted"}, "404": {"description": "Not found"}},
                },
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                },
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": "alive"})
    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": "doomed"})

    delete_operation = schema["/items/{itemId}"]["DELETE"]
    case = delete_operation.Case(path_parameters={"itemId": "doomed"})
    data_source.record_successful_delete(operation=delete_operation, case=case)

    remaining = {inst.data.get("id") for inst in data_source.repository.iter_instances("Item")}
    assert remaining == {"alive"}, "deleted id should be evicted from the pool"

    get_operation = schema["/items/{itemId}"]["GET"]
    drawn = {
        data_source.pick_captured_value(operation=get_operation, location=ParameterLocation.PATH, name="itemId")
        for _ in range(10)
    }
    assert drawn == {"alive"}, "tombstoned id must not be drawn even when it is the highest-weighted candidate"


def test_tombstoned_value_falls_through_when_pool_is_otherwise_empty(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/items/{itemId}": {
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "Deleted"}},
                },
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                },
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": "doomed"})
    assert [inst.data for inst in data_source.repository.iter_instances("Item")] == [{"id": "doomed"}]

    delete_operation = schema["/items/{itemId}"]["DELETE"]
    case = delete_operation.Case(path_parameters={"itemId": "doomed"})
    data_source.record_successful_delete(operation=delete_operation, case=case)

    # Eviction: the deleted entry is gone from the repository, not just deprioritized.
    assert list(data_source.repository.iter_instances("Item")) == []

    # Tombstone: even if the entry were to slip back in, the value is filtered at draw time.
    get_operation = schema["/items/{itemId}"]["GET"]
    assert (
        data_source.pick_captured_value(operation=get_operation, location=ParameterLocation.PATH, name="itemId") is None
    )


def test_record_successful_delete_uses_only_resource_linked_params(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                            "links": {
                                "DeleteItem": {
                                    "operationId": "deleteItem",
                                    "parameters": {"itemId": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            # DELETE has two path params: itemId (resource-linked) and version (not resource-linked)
            "/items/{itemId}/versions/{version}": {
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [
                        {"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "version", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"204": {"description": "Deleted"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": "item-123"})

    delete_operation = schema["/items/{itemId}/versions/{version}"]["DELETE"]
    case = delete_operation.Case(path_parameters={"itemId": "item-123", "version": "v1"})

    data_source.record_successful_delete(operation=delete_operation, case=case)

    # The key should only contain the resource-linked parameter (itemId), not version
    assert len(data_source.usage_tracker._delete_attempts) == 1
    for key in data_source.usage_tracker._delete_attempts.keys():
        assert "itemId" in key
        assert "version" not in key
        assert "v1" not in key


def test_wildcard_status_code_matching(user_schema_builder):
    schema = user_schema_builder(status_code="2XX")
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation=POST_USERS, status_code=201, payload={"id": "1"})
    data_source.repository.record_response(operation=POST_USERS, status_code=404, payload={"id": "2"})

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "1"


@pytest.mark.parametrize(
    ("payloads", "expected"),
    [
        pytest.param(
            [{"id": "valid", "other": ["list", "of", "values"]}],
            ["valid"],
            id="filters-lists",
        ),
        pytest.param(
            [{"id": "valid", "other": {"nested": "dict"}}],
            ["valid"],
            id="filters-dicts",
        ),
        pytest.param(
            [{"id": "valid", "other": None}],
            ["valid"],
            id="filters-none",
        ),
        pytest.param(
            [{"id": "same"}] * 5,
            ["same"],
            id="deduplicates",
        ),
        pytest.param(
            [{"id": str(i)} for i in range(PER_CONTEXT_CAPACITY + 100)],
            PER_CONTEXT_CAPACITY,  # All have same (empty) context, so limited by per-context capacity
            id="respects-capacity-limit",
        ),
    ],
)
def test_collect_values(user_data_source, payloads, expected):
    for payload in payloads:
        user_data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    requirement = ParameterRequirement(USER_RESOURCE, "id")
    values = user_data_source._collect_values(requirement)

    if isinstance(expected, int):
        assert len(values) == expected
    else:
        assert values == expected


@pytest.mark.parametrize(
    ("field", "payloads", "expected_values"),
    [
        pytest.param(
            "tags",
            [{"tags": ["python", "api"]}, {"tags": ["python", "testing"]}, {"tags": ["python", "api"]}],
            [["python", "api"], ["python", "testing"]],
            id="list-deduplication",
        ),
        pytest.param(
            "metadata",
            [
                {"metadata": {"role": "admin", "level": 5}},
                {"metadata": {"level": 5, "role": "admin"}},
                {"metadata": {"role": "user", "level": 1}},
            ],
            [{"role": "admin", "level": 5}, {"role": "user", "level": 1}],
            id="dict-deduplication",
        ),
        pytest.param(
            "id",
            [{"id": "123"}, {"id": 123}, {"id": 123.0}],
            ["123", 123, 123.0],
            id="type-aware-deduplication",
        ),
    ],
)
def test_collect_complex_values(user_data_source, field, payloads, expected_values):
    for payload in payloads:
        user_data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    requirement = ParameterRequirement(USER_RESOURCE, field)
    values = user_data_source._collect_values(requirement)

    assert len(values) == len(expected_values)
    for expected in expected_values:
        assert expected in values


def test_unresolvable_pointer(user_schema_builder):
    array_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            }
        },
    }
    schema = user_schema_builder(response_schema=array_schema)
    data_source = schema.create_extra_data_source()

    payload = {"data": [{"id": "a"}]}
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert resources == []


def test_custom_deserializer(ctx):
    @deserialization.deserializer("text/x-keyvalue")
    def deserialize_keyvalue(ctx, response):
        data = {}
        for line in response.content.decode("utf-8").strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
        return data

    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "content": {
                                "text/x-keyvalue": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                            "links": {
                                "GetUserById": {
                                    "operationId": "getUser",
                                    "parameters": {"user_id": "$response.body#/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    operation = schema["/users"]["POST"]
    case = operation.Case()

    req = requests.Request("POST", "http://test.example/users")
    prepared = req.prepare()
    response = Response(
        status_code=201,
        headers={"content-type": ["text/x-keyvalue"]},
        content=b"id=user123\nname=Alice",
        request=prepared,
        elapsed=0.1,
        verify=True,
    )

    data_source.record_response(operation=operation, response=response, case=case)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "user123"
    assert resources[0].data["name"] == "Alice"


def test_deeper_pointer(user_schema_builder):
    nested_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {
                    "users": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                    }
                },
            }
        },
    }
    schema = user_schema_builder(response_schema=nested_schema)
    data_source = schema.create_extra_data_source()

    payload = {"data": {"users": [{"id": "nested1"}, {"id": "nested2"}]}}
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert {instance.data["id"] for instance in resources} == {"nested1", "nested2"}


def test_prepopulate_from_response_examples(ctx):
    user_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": user_schema,
                                    "example": {"id": "example-user-123", "name": "Example User"},
                                }
                            }
                        }
                    }
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                    },
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    # Without calling record_response(), pool should already have the example value
    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "example-user-123"

    # Verify example is used in captured variants
    get_operation = schema["/users/{user_id}"]["GET"]
    path_schema = get_operation.path_parameters.schema
    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_schema
    )
    assert variants == [{"user_id": "example-user-123"}]


def test_object_level_augmentation_preserves_relationships(ctx):
    user_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    post_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "userId": {"type": "string"}},
        "required": ["id", "userId"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {"201": {"content": {"application/json": {"schema": user_schema}}}},
                }
            },
            "/users/{userId}/posts": {
                "post": {
                    "operationId": "createPost",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"201": {"content": {"application/json": {"schema": post_schema}}}},
                }
            },
            "/users/{userId}/posts/{postId}": {
                "get": {
                    "operationId": "getPost",
                    "parameters": [
                        {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "postId", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"content": {"application/json": {"schema": post_schema}}}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    # Record a post creation with userId in context (from path parameter)
    data_source.repository.record_response(
        operation="POST /users/{userId}/posts",
        status_code=201,
        payload={"id": "post-123", "userId": "user-456"},
        context={"userId": "user-456"},  # Path parameter from request
    )

    # Get the path parameters for GET /users/{userId}/posts/{postId}
    get_operation = schema["/users/{userId}/posts/{postId}"]["GET"]
    path_schema = get_operation.path_parameters.schema

    # get_captured_variants() returns the variants for hybrid strategy
    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_schema
    )
    assert variants is not None
    assert len(variants) == 1

    # Variant contains both userId and postId with correct values
    variant = variants[0]
    assert variant["userId"] == "user-456"
    assert variant["postId"] == "post-123"


def test_context_aware_eviction_maintains_diversity(ctx):
    pet_schema = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}
    schema = ctx.openapi.load_schema(
        {
            "/owners/{ownerId}/pets": {
                "post": {
                    "operationId": "createPet",
                    "parameters": [{"name": "ownerId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"201": {"content": {"application/json": {"schema": pet_schema}}}},
                }
            },
            "/owners/{ownerId}/pets/{id}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {"name": "ownerId", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    # Record pets from multiple owners - more than would fit in a single context bucket
    for owner_id in range(1, 6):
        for pet_id in range(1, 100):
            data_source.repository.record_response(
                operation="POST /owners/{ownerId}/pets",
                status_code=201,
                payload={"id": pet_id + owner_id * 100},
                context={"ownerId": owner_id},
            )

    instances = data_source.repository.iter_instances("Pet")

    # Should have instances from all 5 owners (context diversity preserved)
    owner_ids = {inst.context.get("ownerId") for inst in instances}
    assert len(owner_ids) == 5

    # Each owner should have at most PER_CONTEXT_CAPACITY pets
    for owner_id in range(1, 6):
        owner_pets = [inst for inst in instances if inst.context.get("ownerId") == owner_id]
        assert len(owner_pets) <= PER_CONTEXT_CAPACITY

    # Total instances capped by contexts * per-context capacity
    assert len(instances) <= MAX_CONTEXTS_PER_TYPE * PER_CONTEXT_CAPACITY


def test_negative_aware_strategy_with_captured_values(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    for i in range(5):
        data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": f"item-{i}"})

    operation = schema["/items/{id}"]["GET"]
    config = GenerationConfig()

    strategy = operation.path_parameters.get_strategy(
        operation, config, GenerationMode.NEGATIVE, extra_data_source=data_source
    )

    results = []

    @given(strategy)
    @settings(max_examples=20, database=None)
    def collect_samples(value):
        results.append(value)

    collect_samples()

    assert all(isinstance(r, GeneratedValue) for r in results)


def test_pool_overlay_keeps_required_fields_for_body_without_type_object(ctx):
    # Body schema declares `properties` and `required` but omits `type: object`. The generator
    # may then draw non-dict values (None, scalars) and the captured-variant overlay must not
    # silently coerce those to `{}` and produce a body missing required fields.
    schema = ctx.openapi.load_schema(
        {
            "/clients": {
                "post": {
                    "operationId": "createClient",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"clientId": {"type": "string"}},
                                    }
                                }
                            },
                            "links": {
                                "CreateTask": {
                                    "operationId": "createTask",
                                    "parameters": {"clientId": "$response.body#/clientId"},
                                }
                            },
                        }
                    },
                }
            },
            "/tasks": {
                "post": {
                    "operationId": "createTask",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "clientId": {"type": "string"},
                                        "clientSecret": {"type": "string"},
                                    },
                                    "required": ["clientId", "clientSecret"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    for i in range(5):
        data_source.repository.record_response(
            operation="POST /clients", status_code=201, payload={"clientId": f"client-{i}"}
        )

    operation = schema["/tasks"]["POST"]
    body = operation.body[0]
    config = GenerationConfig()

    strategy = body.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=data_source)

    @given(strategy)
    @settings(max_examples=200, database=None, deadline=None)
    def t(value):
        if isinstance(value, dict):
            for required in ("clientId", "clientSecret"):
                assert required in value, f"required field {required!r} missing from POSITIVE body: {value!r}"

    t()


def test_nested_body_pool_overlay_lands_pool_values(ctx):
    # End-to-end via the body strategy: when a body has a nested object holding a foreign-key
    # field the pool can satisfy, the pool value must reach the wire under the right path.
    schema = ctx.openapi.load_schema(
        {
            "/locations": {
                "post": {
                    "operationId": "createLocation",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/departments": {
                "post": {
                    "operationId": "createDepartment",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "shipping": {
                                            "type": "object",
                                            "properties": {
                                                "location_id": {"type": "integer"},
                                                "note": {"type": "string"},
                                            },
                                            # `note` is required so random nested generation always
                                            # includes it; under deep-merge the overlay must keep
                                            # it in place when it injects `location_id`.
                                            "required": ["note"],
                                        },
                                    },
                                    "required": ["shipping"],
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    pooled_ids = {1, 2, 3}
    for i in pooled_ids:
        data_source.repository.record_response(operation="POST /locations", status_code=201, payload={"id": i})

    operation = schema["/departments"]["POST"]
    body = operation.body[0]
    config = GenerationConfig()
    strategy = body.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=data_source)

    pool_hits_with_note = 0
    pool_hits = 0

    @given(strategy)
    @settings(max_examples=100, database=None, deadline=None)
    def collect(value):
        nonlocal pool_hits_with_note, pool_hits
        if not isinstance(value, dict):
            return
        shipping = value.get("shipping")
        if not isinstance(shipping, dict):
            return
        if shipping.get("location_id") in pooled_ids:
            pool_hits += 1
            if "note" in shipping:
                pool_hits_with_note += 1

    collect()

    assert pool_hits > 0, "Pool's Location.id values never landed under shipping.location_id"
    # Deep-merge guard: when the overlay injects location_id, the generated `note`
    # sibling (required by the nested schema) must survive the merge.
    assert pool_hits_with_note == pool_hits, (
        f"{pool_hits - pool_hits_with_note} of {pool_hits} pool-overlaid bodies dropped the "
        "generated `note` sibling — nested overlay replaced the whole `shipping` object."
    )


def test_negative_aware_strategy_with_captured_values_body(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/projects": {
                "post": {
                    "operationId": "createProject",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                            "links": {
                                "CreateTask": {
                                    "operationId": "createTask",
                                    "parameters": {"project_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/tasks": {
                "post": {
                    "operationId": "createTask",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "title": {"type": "string"}},
                                    "required": ["project_id"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    for i in range(5):
        data_source.repository.record_response(
            operation="POST /projects", status_code=201, payload={"id": f"project-{i}"}
        )

    operation = schema["/tasks"]["POST"]
    config = GenerationConfig()
    body = operation.body[0]

    strategy = body.get_strategy(operation, config, GenerationMode.NEGATIVE, extra_data_source=data_source)

    results = []

    @given(strategy)
    @settings(max_examples=20, database=None)
    def collect_samples(value):
        results.append(value)

    collect_samples()

    assert all(isinstance(r, GeneratedValue) for r in results)


def test_primitive_identifier_extraction(ctx):
    recipe_schema = {
        "type": "object",
        "properties": {"slug": {"type": "string"}, "name": {"type": "string"}},
        "required": ["slug"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/recipes": {
                "post": {
                    "operationId": "createRecipe",
                    "responses": {"201": {"content": {"application/json": {"schema": {"type": "string"}}}}},
                }
            },
            "/recipes/{slug}": {
                "get": {
                    "operationId": "getRecipe",
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": recipe_schema}}}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /recipes", status_code=201, payload="my-recipe-slug")

    resources = list(data_source.repository.iter_instances("Recipe"))
    assert len(resources) == 1
    assert resources[0].data == {"slug": "my-recipe-slug"}

    get_operation = schema["/recipes/{slug}"]["GET"]
    path_schema = get_operation.path_parameters.schema
    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_schema
    )
    assert variants == [{"slug": "my-recipe-slug"}]


def test_primitive_identifier_adds_field_to_empty_resource(ctx):
    # GET with empty object schema creates Item with no fields, POST adds "id"
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {"responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}}},
                "post": {"responses": {"201": {"content": {"application/json": {"schema": {"type": "string"}}}}}},
            },
            "/items/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
        }
    )

    # Verify resource has "id" field added by POST
    graph = schema.analysis.dependency_graph
    assert "id" in graph.resources["Item"].fields

    # Verify primitive capture works
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(operation="POST /items", status_code=201, payload="item-123")

    resources = list(data_source.repository.iter_instances("Item"))
    assert len(resources) == 1
    assert resources[0].data == {"id": "item-123"}


def test_primitive_response_ignored_for_root_path(ctx):
    # POST at "/" can't derive resource name, should produce no outputs
    schema = ctx.openapi.load_schema(
        {"/": {"post": {"responses": {"201": {"content": {"application/json": {"schema": {"type": "string"}}}}}}}}
    )

    # Verify no outputs for POST / in dependency graph
    graph = schema.analysis.dependency_graph
    operation = graph.operations.get("POST /")
    assert operation is None or operation.outputs == []


def test_identifier_field_fallback_when_paths_differ(ctx):
    # Producer at /recipes, consumer at /admin/recipes/{slug} - different base paths
    schema = ctx.openapi.load_schema(
        {
            "/recipes": {
                "post": {"responses": {"201": {"content": {"application/json": {"schema": {"type": "string"}}}}}}
            },
            "/admin/recipes/{slug}": {
                "get": {
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"slug": {"type": "string"}}}
                                }
                            }
                        }
                    },
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /recipes", status_code=201, payload="my-recipe")

    # Should use "slug" from fallback (first consumer's field)
    resources = list(data_source.repository.iter_instances("Recipe"))
    assert len(resources) == 1
    assert resources[0].data == {"slug": "my-recipe"}


def test_primitive_integer_identifier(ctx):
    # POST returning integer ID
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {"responses": {"201": {"content": {"application/json": {"schema": {"type": "integer"}}}}}}
            },
            "/users/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            }
                        }
                    },
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation="POST /users", status_code=201, payload=12345)

    resources = list(data_source.repository.iter_instances("User"))
    assert len(resources) == 1
    assert resources[0].data == {"id": 12345}


def test_pick_captured_value_returns_none_for_unbound_parameter(user_schema_builder):
    schema = user_schema_builder()
    data_source = schema.create_extra_data_source()

    operation = schema["/users"]["POST"]
    result = data_source.pick_captured_value(operation=operation, location=ParameterLocation.PATH, name="user_id")
    assert result is None


def test_pick_captured_value_returns_none_for_empty_pool(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    get_endpoint = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=get_endpoint)
    data_source = schema.create_extra_data_source()

    get_operation = schema["/users/{user_id}"]["GET"]
    result = data_source.pick_captured_value(operation=get_operation, location=ParameterLocation.PATH, name="user_id")
    assert result is None


def test_pick_captured_value_returns_value_when_pool_has_data(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    get_endpoint = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=get_endpoint)
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "1", "name": "Alice"}
    )

    get_operation = schema["/users/{user_id}"]["GET"]
    result = data_source.pick_captured_value(operation=get_operation, location=ParameterLocation.PATH, name="user_id")
    assert result == "1"


def test_pick_captured_value_rotates_across_consecutive_picks(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    get_endpoint = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=get_endpoint)
    data_source = schema.create_extra_data_source()

    for value in ("a", "b", "c", "d"):
        data_source.repository.record_response(
            operation=POST_USERS, status_code=CREATED, payload={"id": value, "name": "x"}
        )

    get_operation = schema["/users/{user_id}"]["GET"]
    picks = [
        data_source.pick_captured_value(operation=get_operation, location=ParameterLocation.PATH, name="user_id")
        for _ in range(4)
    ]
    # Deterministic rotation: each draw deprioritizes the chosen variant, so
    # the first four picks cover all four values.
    assert set(picks) == {"a", "b", "c", "d"}


def test_pick_correlated_values_empty_for_unbound_operation(user_schema_builder):
    schema = user_schema_builder()
    data_source = schema.create_extra_data_source()
    operation = schema["/users"]["POST"]
    assert data_source.pick_correlated_values(operation=operation) == {}


def test_pick_correlated_values_single_family_correlated_pair(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    nested = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": user_schema}}}},
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=nested)
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "1", "name": "Alice"}
    )
    operation = schema["/users/{user_id}"]["GET"]
    result = data_source.pick_correlated_values(operation=operation)
    assert result == {(ParameterLocation.PATH, "user_id"): "1"}


def test_pick_correlated_values_falls_back_when_family_lacks_full_match(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    nested = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": user_schema}}}},
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=nested)
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "1", "name": "Alice"}
    )
    operation = schema["/users/{user_id}"]["GET"]
    result = data_source.pick_correlated_values(operation=operation)
    assert (ParameterLocation.PATH, "user_id") in result


def test_pick_correlated_values_rotates_across_consecutive_calls(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    nested = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": user_schema}}}},
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=nested)
    data_source = schema.create_extra_data_source()
    for value in ("a", "b", "c", "d"):
        data_source.repository.record_response(
            operation=POST_USERS, status_code=CREATED, payload={"id": value, "name": "x"}
        )
    operation = schema["/users/{user_id}"]["GET"]
    picks = [
        data_source.pick_correlated_values(operation=operation)[(ParameterLocation.PATH, "user_id")] for _ in range(4)
    ]
    assert set(picks) == {"a", "b", "c", "d"}


def test_correlated_and_per_slot_share_rotation_state(user_schema_builder):
    # Correlated and per-slot picks must deprioritize the same instance for cross-phase rotation.
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    nested = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": user_schema}}}},
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=nested)
    data_source = schema.create_extra_data_source()
    for value in ("a", "b"):
        data_source.repository.record_response(
            operation=POST_USERS, status_code=CREATED, payload={"id": value, "name": "x"}
        )
    operation = schema["/users/{user_id}"]["GET"]
    correlated = data_source.pick_correlated_values(operation=operation)
    drawn_id = correlated[(ParameterLocation.PATH, "user_id")]
    next_pick = data_source.pick_captured_value(operation=operation, location=ParameterLocation.PATH, name="user_id")
    assert next_pick != drawn_id
