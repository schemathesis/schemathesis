from __future__ import annotations

import json

import jsonschema_rs
import pytest
from flask import Response, jsonify, request
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemathesis.core import transport
from schemathesis.core.deserialization import register_deserializer
from schemathesis.generation.modes import GenerationMode
from schemathesis.generation.value import GeneratedValue
from schemathesis.resources import SemanticDraw
from schemathesis.specs.openapi.adapter.parameters import (
    _MISSING,
    _get_at_path,
    _set_at_path,
    build_semantic_overlay,
)
from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource
from schemathesis.specs.openapi.formats import HEADER_FORMAT
from schemathesis.specs.openapi.semantic_pool import (
    BoundedValues,
    IngestionLeaf,
    LeafDescriptor,
    SemanticCandidate,
    SemanticValueIndex,
    is_pool_eligible,
    iter_consumer_leaves,
    iter_ingestion_leaves,
    pattern_hash,
)


def test_pattern_hash_is_stable_for_equal_inputs():
    assert pattern_hash("^foo$") == pattern_hash("^foo$")


def test_pattern_hash_differs_for_different_inputs():
    assert pattern_hash("^foo$") != pattern_hash("^bar$")


@pytest.mark.parametrize(
    ("format_token", "expected"),
    [("email", True), ("uuid", False), (None, True)],
    ids=["listed-format", "uuid-rejected", "no-format"],
)
def test_is_pool_eligible_string(format_token, expected):
    assert is_pool_eligible(type_token="string", format_token=format_token) is expected


def test_bounded_values_add_dedups():
    bounded = BoundedValues(max_size=10)
    bounded.add("a", "GET /producer")
    bounded.add("a", "GET /producer")
    bounded.add("b", "GET /producer")
    assert bounded.values() == ("a", "b")


def test_bounded_values_evicts_oldest_above_cap():
    bounded = BoundedValues(max_size=3)
    for value in ["a", "b", "c", "d"]:
        bounded.add(value, "GET /producer")
    assert bounded.values() == ("b", "c", "d")


def test_bounded_values_record_draw_does_not_change_order():
    bounded = BoundedValues(max_size=10)
    bounded.add("a", "GET /producer")
    bounded.add("b", "GET /producer")
    bounded.record_draw("a")
    assert bounded.values() == ("a", "b")


def test_bounded_values_preserves_first_source_operation():
    bounded = BoundedValues(max_size=10)
    bounded.add("a", "GET /first")
    bounded.add("a", "GET /second")
    assert bounded.entries() == (SemanticCandidate("a", "GET /first"),)


@pytest.mark.parametrize(
    ("format_token", "pattern_hash_value", "normalized_name", "expected_key"),
    [
        ("email", None, "email", ("by_format", ("string", "email"))),
        (None, "abc123", "phone", ("by_pattern", ("string", "abc123"))),
        (None, None, "cityname", ("by_name", ("string", "cityname"))),
    ],
    ids=["format-bucket", "pattern-bucket", "name-bucket"],
)
def test_index_routes_to_expected_bucket(format_token, pattern_hash_value, normalized_name, expected_key):
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=format_token,
        pattern_hash=pattern_hash_value,
        normalized_name=normalized_name,
        value="value",
        source_operation="GET /producer",
    )
    bucket_name, key = expected_key
    buckets = {"by_format": index.by_format, "by_pattern": index.by_pattern, "by_name": index.by_name}
    assert key in buckets[bucket_name]
    for other_name, other_bucket in buckets.items():
        if other_name != bucket_name:
            assert not other_bucket, f"value leaked into {other_name}"


def test_lookup_priority_format_beats_name():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="a@b.com",
        source_operation="GET /users",
    )
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash=None,
        normalized_name="email",
        value="not-email",
        source_operation="GET /users",
    )
    assert index.lookup(type_token="string", format_token="email", pattern_hash=None, normalized_name="email") == (
        SemanticCandidate("a@b.com", "GET /users"),
    )


def test_lookup_falls_back_to_pattern_when_format_empty():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash="hash",
        normalized_name=None,
        value="v1",
        source_operation="GET /producer",
    )
    assert index.lookup(type_token="string", format_token="email", pattern_hash="hash", normalized_name=None) == (
        SemanticCandidate("v1", "GET /producer"),
    )


def test_lookup_returns_empty_tuple_when_no_match():
    index = SemanticValueIndex()
    assert index.lookup(type_token="string", format_token="email", pattern_hash=None, normalized_name="email") == ()


def test_index_add_tags_value_with_source_operation():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="alice@example.com",
        source_operation="POST /api/users",
    )
    assert index.lookup(type_token="string", format_token="email", pattern_hash=None, normalized_name="email") == (
        SemanticCandidate("alice@example.com", "POST /api/users"),
    )


def test_walker_yields_email_from_object_property():
    schema = {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}}
    body = {"email": "alice@example.com"}
    assert list(iter_ingestion_leaves(schema, body)) == [
        IngestionLeaf("string", "email", None, "email", "alice@example.com")
    ]


def test_walker_recurses_into_nested_object():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "email"}},
            }
        },
    }
    body = {"user": {"email": "a@b.com"}}
    assert list(iter_ingestion_leaves(schema, body)) == [IngestionLeaf("string", "email", None, "email", "a@b.com")]


def test_walker_skips_excluded_names():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "format": "email"},
            "email": {"type": "string", "format": "email"},
        },
    }
    body = {"id": "x@y.com", "email": "a@b.com"}
    assert list(iter_ingestion_leaves(schema, body, excluded_names=frozenset({"id"}))) == [
        IngestionLeaf("string", "email", None, "email", "a@b.com")
    ]


def test_walker_skips_uuid_format_via_allowlist():
    schema = {"type": "object", "properties": {"correlationId": {"type": "string", "format": "uuid"}}}
    body = {"correlationId": "550e8400-e29b-41d4-a716-446655440000"}
    assert list(iter_ingestion_leaves(schema, body)) == []


def test_walker_skips_null_values():
    schema = {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}}
    body = {"email": None}
    assert list(iter_ingestion_leaves(schema, body)) == []


def test_walker_handles_nullable_type_list():
    schema = {
        "type": "object",
        "properties": {"email": {"type": ["string", "null"], "format": "email"}},
    }
    body = {"email": "a@b.com"}
    assert list(iter_ingestion_leaves(schema, body)) == [IngestionLeaf("string", "email", None, "email", "a@b.com")]


def test_walker_resolves_all_of_inheritance():
    schema = {
        "allOf": [
            {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}},
            {"type": "object", "properties": {"name": {"type": "string"}}},
        ]
    }
    body = {"email": "a@b.com", "name": "Alice"}
    assert list(iter_ingestion_leaves(schema, body)) == [
        IngestionLeaf("string", "email", None, "email", "a@b.com"),
        IngestionLeaf("string", None, None, "name", "Alice"),
    ]


def test_walker_resolves_one_of_to_branch_with_properties():
    schema = {
        "oneOf": [
            {"type": "string"},
            {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}},
        ]
    }
    body = {"email": "a@b.com"}
    assert list(iter_ingestion_leaves(schema, body)) == [IngestionLeaf("string", "email", None, "email", "a@b.com")]


def test_walker_excludes_bool_from_integer_pool():
    # `isinstance(True, int)` is True in Python; integer pools must not silently absorb booleans.
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer", "format": "int32", "minimum": 0, "maximum": 100}},
    }
    body = {"count": True}
    assert list(iter_ingestion_leaves(schema, body)) == []


@pytest.mark.parametrize(
    ("leaf_schema", "value"),
    [
        ({"type": "integer"}, 42),
        ({"type": "integer", "format": "int32"}, 100),
        ({"type": "integer", "format": "int64"}, 1234567890),
        ({"type": "number"}, 3.14),
        ({"type": "number", "format": "double"}, 2.71),
    ],
    ids=["unbounded-integer", "int32-no-bounds", "int64-no-bounds", "unbounded-number", "double-no-bounds"],
)
def test_walker_skips_unbounded_numeric(leaf_schema, value):
    # Numeric formats only declare bit width; without `minimum`/`maximum` we cannot tell
    # unrelated numeric domains apart and pooling them creates cross-resource noise.
    schema = {"type": "object", "properties": {"x": leaf_schema}}
    assert list(iter_ingestion_leaves(schema, {"x": value})) == []


@pytest.mark.parametrize(
    ("leaf_schema", "value", "expected_type"),
    [
        ({"type": "integer", "minimum": 0, "maximum": 5}, 4, "integer"),
        ({"type": "integer", "exclusiveMinimum": 0}, 7, "integer"),
        ({"type": "number", "exclusiveMaximum": 1}, 0.75, "number"),
    ],
    ids=["min-max", "exclusive-minimum", "exclusive-maximum"],
)
def test_walker_ingests_bounded_numeric(leaf_schema, value, expected_type):
    schema = {"type": "object", "properties": {"x": leaf_schema}}
    assert list(iter_ingestion_leaves(schema, {"x": value})) == [IngestionLeaf(expected_type, None, None, "x", value)]


def test_walker_does_not_recurse_into_arrays():
    schema = {
        "type": "object",
        "properties": {"emails": {"type": "array", "items": {"type": "string", "format": "email"}}},
    }
    body = {"emails": ["a@b.com", "c@d.com"]}
    assert list(iter_ingestion_leaves(schema, body)) == []


def test_walker_records_pattern_when_format_absent():
    schema = {"type": "object", "properties": {"phone": {"type": "string", "pattern": "^\\+\\d+"}}}
    assert list(iter_ingestion_leaves(schema, {"phone": "+12025551234"})) == [
        IngestionLeaf("string", None, pattern_hash("^\\+\\d+"), "phone", "+12025551234")
    ]


def test_walker_falls_back_to_name_when_no_format_or_pattern():
    schema = {"type": "object", "properties": {"cityName": {"type": "string"}}}
    body = {"cityName": "Berlin"}
    assert list(iter_ingestion_leaves(schema, body)) == [IngestionLeaf("string", None, None, "cityname", "Berlin")]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"contact": "alice@example.com"},
            [IngestionLeaf("string", "email", None, "contact", "alice@example.com")],
        ),
        ({"correlationId": "550e8400-e29b-41d4-a716-446655440000"}, []),
        (
            {"description": "lorem ipsum dolor sit"},
            [IngestionLeaf("string", None, None, "description", "lorem ipsum dolor sit")],
        ),
        # Without a schema we cannot read numeric bounds, so numerics and booleans are skipped.
        ({"count": 42, "ratio": 1.5, "enabled": True}, []),
    ],
    ids=["email-shape-inferred", "uuid-shape-skipped", "freeform-name-only", "numeric-and-boolean-skipped"],
)
def test_walker_schemaless(body, expected):
    assert list(iter_ingestion_leaves(None, body)) == expected


def test_walker_respects_max_depth():
    schema = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {
                    "b": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "format": "email"}},
                    }
                },
            }
        },
    }
    body = {"a": {"b": {"email": "x@y.com"}}}
    assert list(iter_ingestion_leaves(schema, body, max_depth=1)) == []
    assert list(iter_ingestion_leaves(schema, body, max_depth=10)) != []


def test_walker_respects_max_nodes():
    schema = {
        "type": "object",
        "properties": {f"prop_{i}": {"type": "string", "format": "email"} for i in range(20)},
    }
    body = {f"prop_{i}": f"u{i}@example.com" for i in range(20)}
    leaves = list(iter_ingestion_leaves(schema, body, max_nodes=3))
    assert 0 < len(leaves) < 20


def test_consumer_walker_yields_descriptor_per_primitive_property():
    schema = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "name": {"type": "string"},
        },
    }
    assert {d.path for d in iter_consumer_leaves(schema)} == {("email",), ("name",)}


def test_consumer_walker_skips_const_and_enum_leaves():
    # Fixed-domain leaves are not substitutable slots: a pool value would violate the constraint
    # (e.g. a `type` discriminator), producing data the API rejects.
    schema = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "const": "do-nothing"},
            "status": {"type": "string", "enum": ["active", "paused"]},
            "name": {"type": "string"},
        },
    }
    assert [d.path for d in iter_consumer_leaves(schema)] == [("name",)]


def test_walker_skips_const_and_enum_values():
    schema = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "const": "do-nothing"},
            "status": {"type": "string", "enum": ["active", "paused"]},
            "name": {"type": "string"},
        },
    }
    body = {"type": "do-nothing", "status": "active", "name": "alice"}
    assert list(iter_ingestion_leaves(schema, body)) == [IngestionLeaf("string", None, None, "name", "alice")]


def test_consumer_walker_records_path_for_nested_property():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "email"}},
            }
        },
    }
    assert iter_consumer_leaves(schema) == [
        LeafDescriptor(
            path=("user", "email"),
            type="string",
            format="email",
            pattern_hash=None,
            normalized_name="email",
            schema={"type": "string", "format": "email"},
        )
    ]


def test_consumer_walker_includes_uuid_descriptors():
    # Index gates by allowlist; consumer descriptors stay symmetric so lookup just returns empty.
    schema = {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}}
    [descriptor] = iter_consumer_leaves(schema)
    assert descriptor.format == "uuid"


_PRODUCER_SCHEMA = {
    "/api/produce": {
        "get": {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"contact": {"type": "string", "format": "email"}},
                            }
                        }
                    },
                }
            }
        }
    }
}


def test_extra_data_source_built_when_schema_has_successful_response(ctx):
    schema = ctx.openapi.load_schema(_PRODUCER_SCHEMA)
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert extra_data_source.semantic_index is not None
    assert "GET /api/produce" in extra_data_source.semantic_eligible_operations


def test_extra_data_source_skipped_when_no_successful_responses(ctx):
    schema = ctx.openapi.load_schema({"/api/error": {"get": {"responses": {"500": {"description": "Server error"}}}}})
    assert schema.analysis.extra_data_source is None


def _ingest_2xx(operation, *, body, extra_data_source, case_factory, response_factory):
    case = case_factory(operation=operation)
    response = transport.Response.from_any(
        response_factory.requests(
            content=json.dumps(body).encode(),
            content_type="application/json",
            status_code=200,
        )
    )
    extra_data_source.record_response(operation=operation, response=response, case=case)


def _lookup_by_descriptor(index, descriptor):
    return tuple(
        candidate.value
        for candidate in index.lookup(
            type_token=descriptor.type,
            format_token=descriptor.format,
            pattern_hash=descriptor.pattern_hash,
            normalized_name=descriptor.normalized_name,
        )
    )


def _extra_data_source_for(ctx, schema_dict):
    schema = ctx.openapi.load_schema(schema_dict)
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert extra_data_source.semantic_index is not None
    return schema, extra_data_source


def test_record_response_populates_semantic_index(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(ctx, _PRODUCER_SCHEMA)
    operation = schema["/api/produce"]["GET"]
    _ingest_2xx(
        operation,
        body={"contact": "alice@example.com"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    assert extra_data_source.semantic_index.lookup(
        type_token="string", format_token="email", pattern_hash=None, normalized_name="contact"
    ) == (SemanticCandidate("alice@example.com", operation.label),)


def test_record_response_skips_non_2xx(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(ctx, _PRODUCER_SCHEMA)
    operation = schema["/api/produce"]["GET"]
    case = case_factory(operation=operation)
    response = transport.Response.from_any(
        response_factory.requests(
            content=b'{"contact": "poison@example.com"}',
            content_type="application/json",
            status_code=400,
        )
    )
    extra_data_source.record_response(operation=operation, response=response, case=case)
    assert (
        extra_data_source.semantic_index.lookup(
            type_token="string", format_token="email", pattern_hash=None, normalized_name="contact"
        )
        == ()
    )


def test_record_response_excludes_path_parameter_names(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/items/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "format": "email"},
                                            "owner": {"type": "string", "format": "email"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    )
    operation = schema["/api/items/{id}"]["GET"]
    case = case_factory(operation=operation, path_parameters={"id": "42"})
    response = transport.Response.from_any(
        response_factory.requests(
            content=b'{"id": "x@y.com", "owner": "owner@example.com"}',
            content_type="application/json",
            status_code=200,
        )
    )
    extra_data_source.record_response(operation=operation, response=response, case=case)
    pool_values = {
        candidate.value
        for candidate in extra_data_source.semantic_index.lookup(
            type_token="string", format_token="email", pattern_hash=None, normalized_name="email"
        )
    }
    assert "x@y.com" not in pool_values
    assert "owner@example.com" in pool_values


def test_scenario_email_cross_operation_flow(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"email": {"type": "string", "format": "email"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/api/users"]["GET"],
        body={"email": "alice@example.com"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {
        "type": "object",
        "properties": {"recipient_email": {"type": "string", "format": "email"}},
    }
    [descriptor] = iter_consumer_leaves(consumer)
    assert "alice@example.com" in _lookup_by_descriptor(extra_data_source.semantic_index, descriptor)


def test_scenario_currency_named_fallback_cross_operation_flow(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/products": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"currency": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/api/products"]["GET"],
        body={"currency": "EUR"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {"type": "object", "properties": {"currency": {"type": "string"}}}
    [descriptor] = iter_consumer_leaves(consumer)
    assert "EUR" in _lookup_by_descriptor(extra_data_source.semantic_index, descriptor)


def test_scenario_date_time_cross_operation_flow(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/events": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "start_at": {"type": "string", "format": "date-time"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/api/events"]["GET"],
        body={"start_at": "2026-05-08T10:00:00Z"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {
        "type": "object",
        "properties": {"start_at": {"type": "string", "format": "date-time"}},
    }
    [descriptor] = iter_consumer_leaves(consumer)
    assert "2026-05-08T10:00:00Z" in _lookup_by_descriptor(extra_data_source.semantic_index, descriptor)


def test_scenario_url_cross_operation_flow(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/webhooks": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"url": {"type": "string", "format": "uri"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/api/webhooks"]["GET"],
        body={"url": "https://example.com/cb"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {
        "type": "object",
        "properties": {"callback_url": {"type": "string", "format": "uri"}},
    }
    [descriptor] = iter_consumer_leaves(consumer)
    assert "https://example.com/cb" in _lookup_by_descriptor(extra_data_source.semantic_index, descriptor)


def test_restgym_flight_search_departure_time_cross_operation(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/v1/flights": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "departureTime": {"type": "string", "format": "date-time"},
                                            "arrivalTime": {"type": "string", "format": "date-time"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/api/v1/flights"]["GET"],
        body={"departureTime": "2026-05-08T08:00:00Z", "arrivalTime": "2026-05-08T11:00:00Z"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {
        "type": "object",
        "required": ["departureTime"],
        "properties": {
            "departureTime": {"type": "string", "format": "date-time"},
            "arrivalTime": {"type": "string", "format": "date-time"},
        },
    }
    departure = next(d for d in iter_consumer_leaves(consumer) if d.normalized_name == "departuretime")
    assert "2026-05-08T08:00:00Z" in _lookup_by_descriptor(extra_data_source.semantic_index, departure)


def test_restgym_traccar_device_time_cross_operation(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/positions": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "deviceTime": {"type": "string", "format": "date-time"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/positions"]["GET"],
        body={"deviceTime": "2026-05-08T12:00:00Z"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {
        "type": "object",
        "properties": {
            "from": {"type": "string", "format": "date-time"},
            "to": {"type": "string", "format": "date-time"},
        },
    }
    descriptors = iter_consumer_leaves(consumer)
    assert all(d.format == "date-time" for d in descriptors)
    [pool] = {tuple(_lookup_by_descriptor(extra_data_source.semantic_index, d)) for d in descriptors}
    assert "2026-05-08T12:00:00Z" in pool


def test_restgym_market_name_query_cross_operation(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/customer": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/customer"]["GET"],
        body={"name": "alice"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {"type": "object", "properties": {"name": {"type": "string"}}}
    [descriptor] = iter_consumer_leaves(consumer)
    assert "alice" in _lookup_by_descriptor(extra_data_source.semantic_index, descriptor)


def test_restgym_pet_clinic_last_name_query_cross_operation(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/petclinic/api/owners": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"lastName": {"type": "string"}}}
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
                                        "properties": {"lastName": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    )
    _ingest_2xx(
        schema["/petclinic/api/owners"]["POST"],
        body={"lastName": "Franklin"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    consumer = {"type": "object", "properties": {"lastName": {"type": "string"}}}
    [descriptor] = iter_consumer_leaves(consumer)
    assert "Franklin" in _lookup_by_descriptor(extra_data_source.semantic_index, descriptor)


def test_restgym_user_management_email_login_chain(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "email": {"type": "string", "format": "email"},
                                            "country": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    )
    _ingest_2xx(
        schema["/users"]["GET"],
        body={"email": "user@example.com", "country": "DE"},
        extra_data_source=extra_data_source,
        case_factory=case_factory,
        response_factory=response_factory,
    )
    [email_desc] = iter_consumer_leaves(
        {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}}
    )
    assert "user@example.com" in _lookup_by_descriptor(extra_data_source.semantic_index, email_desc)
    [country_desc] = iter_consumer_leaves({"type": "object", "properties": {"country": {"type": "string"}}})
    assert "DE" in _lookup_by_descriptor(extra_data_source.semantic_index, country_desc)


_HARVESTED_EMAIL = "harvested@example.com"
_HARVESTED_CURRENCY = "EUR"
_HARVESTED_DATETIME = "2026-05-08T10:00:00Z"
_HARVESTED_URL = "https://example.com/cb"


def _attach_planted_bug(app, producer_path, producer_payload, consumer_path, trigger):
    @app.route(producer_path)
    def _producer():
        return jsonify(producer_payload)

    @app.route(consumer_path, methods=["POST"])
    def _consumer():
        body = request.get_json(silent=True) or {}
        if trigger(body):
            raise RuntimeError("planted bug")
        return Response(status=200)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_planted_bug_surfaces_via_email_format_pool(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/contacts": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"email": {"type": "string", "format": "email"}},
                                        "required": ["email"],
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/api/messages": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"recipient_email": {"type": "string", "format": "email"}},
                                    "required": ["recipient_email"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    _attach_planted_bug(
        app,
        "/api/contacts",
        {"email": _HARVESTED_EMAIL},
        "/api/messages",
        lambda body: body.get("recipient_email") == _HARVESTED_EMAIL,
    )
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=30",
            "--seed=42",
            "--checks=not_a_server_error",
            "--suppress-health-check=filter_too_much",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_planted_bug_surfaces_via_named_fallback_pool(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/catalog": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"currency": {"type": "string"}},
                                        "required": ["currency"],
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/api/orders": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"currency": {"type": "string"}},
                                    "required": ["currency"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    _attach_planted_bug(
        app,
        "/api/catalog",
        {"currency": _HARVESTED_CURRENCY},
        "/api/orders",
        lambda body: body.get("currency") == _HARVESTED_CURRENCY,
    )
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=30",
            "--seed=42",
            "--checks=not_a_server_error",
            "--suppress-health-check=filter_too_much",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_planted_bug_surfaces_via_date_time_format_pool(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/calendar": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"start_at": {"type": "string", "format": "date-time"}},
                                        "required": ["start_at"],
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/api/events": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"start_at": {"type": "string", "format": "date-time"}},
                                    "required": ["start_at"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    _attach_planted_bug(
        app,
        "/api/calendar",
        {"start_at": _HARVESTED_DATETIME},
        "/api/events",
        lambda body: body.get("start_at") == _HARVESTED_DATETIME,
    )
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=30",
            "--seed=42",
            "--checks=not_a_server_error",
            "--suppress-health-check=filter_too_much",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_planted_bug_surfaces_via_uri_format_pool(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/integrations": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"url": {"type": "string", "format": "uri"}},
                                        "required": ["url"],
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/api/webhooks": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"callback_url": {"type": "string", "format": "uri"}},
                                    "required": ["callback_url"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    _attach_planted_bug(
        app,
        "/api/integrations",
        {"url": _HARVESTED_URL},
        "/api/webhooks",
        lambda body: isinstance(body, dict) and body.get("callback_url") == _HARVESTED_URL,
    )
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=30",
            "--seed=42",
            "--checks=not_a_server_error",
            "--suppress-health-check=filter_too_much",
        )
        == snapshot_cli
    )


def test_overlay_skips_candidate_violating_consumer_constraint():
    # Consumer's `maxLength` is not encoded in the index keys; the validity gate must reject the substitution.
    too_long = "a" * 60 + "@example.com"
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value=too_long,
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("recipient_email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email", "maxLength": 30},
    )
    inner = st.fixed_dictionaries({"recipient_email": st.just("ok@example.com")})
    overlay = build_semantic_overlay(inner, [descriptor], index, jsonschema_rs.Draft202012Validator)

    drawn: list[dict] = []

    @given(overlay)
    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def collect(value):
        drawn.append(value)

    collect()
    assert drawn, "no values were drawn"
    assert all(v["recipient_email"] != too_long for v in drawn), (
        f"validity gate should have rejected the too-long substitution; saw {drawn}"
    )


def test_overlay_substitution_into_header_filtered_before_transport(ctx, cli):
    # Non-Latin-1 substitutions into header parameters must be filtered before transport, not after.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/source": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"xTag": {"type": "string"}},
                                        "required": ["xTag"],
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/api/target": {
                "get": {
                    "parameters": [
                        {
                            "name": "X-Tag",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/api/source")
    def source():
        return jsonify({"xTag": "用户"})

    @app.route("/api/target")
    def target():
        return Response(status=200)

    result = cli.run_openapi_app(
        app,
        "--max-examples=30",
        "--seed=42",
        "--phases=fuzzing",
        "--mode=positive",
        "--checks=not_a_server_error",
        "--suppress-health-check=filter_too_much",
    )
    assert "UnicodeEncodeError" not in result.stdout, (
        "non-Latin-1 substitution bypassed is_valid_header; HTTP client raised UnicodeEncodeError"
    )


def test_walker_resolves_bundled_ref_in_property():
    schema = {
        "type": "object",
        "properties": {"email": {"$ref": "#/x-bundled/Email"}},
        "x-bundled": {"Email": {"type": "string", "format": "email"}},
    }
    body = {"email": "a@b.com"}
    assert list(iter_ingestion_leaves(schema, body)) == [IngestionLeaf("string", "email", None, "email", "a@b.com")]


def test_consumer_walker_resolves_bundled_ref_in_property():
    schema = {
        "type": "object",
        "properties": {"recipient_email": {"$ref": "#/x-bundled/Email"}},
        "x-bundled": {"Email": {"type": "string", "format": "email"}},
    }
    [descriptor] = iter_consumer_leaves(schema)
    assert descriptor.path == ("recipient_email",)
    assert descriptor.format == "email"


def test_consumer_walker_descends_into_property_only_object():
    schema = {"properties": {"email": {"type": "string", "format": "email"}}}
    [descriptor] = iter_consumer_leaves(schema)
    assert descriptor.path == ("email",)
    assert descriptor.format == "email"


def test_lookup_blocks_name_fallback_for_excluded_format():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash=None,
        normalized_name="id",
        value="550e8400-e29b-41d4-a716-446655440000",
        source_operation="GET /producer",
    )
    assert index.lookup(type_token="string", format_token="uuid", pattern_hash=None, normalized_name="id") == ()


def test_consumer_walker_resolves_ref_branches_in_all_of():
    schema = {
        "allOf": [{"$ref": "#/x-bundled/User"}],
        "x-bundled": {
            "User": {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}},
        },
    }
    [descriptor] = iter_consumer_leaves(schema)
    assert descriptor.path == ("email",)
    assert descriptor.format == "email"


@pytest.mark.parametrize(
    "leaf_schema",
    [
        {"type": "integer"},
        {"type": "integer", "format": "int64"},
        {"type": "number"},
        {"type": "number", "format": "double"},
    ],
    ids=["unbounded-integer", "int64-no-bounds", "unbounded-number", "double-no-bounds"],
)
def test_consumer_walker_skips_unbounded_numeric(leaf_schema):
    schema = {"type": "object", "properties": {"x": leaf_schema}}
    assert iter_consumer_leaves(schema) == []


@pytest.mark.parametrize(
    ("leaf_schema", "expected_type"),
    [
        ({"type": "integer", "minimum": 0, "maximum": 5}, "integer"),
        ({"type": "number", "exclusiveMaximum": 1}, "number"),
    ],
    ids=["int-min-max", "number-exclusive-max"],
)
def test_consumer_walker_emits_descriptor_for_bounded_numeric(leaf_schema, expected_type):
    schema = {"type": "object", "properties": {"x": leaf_schema}}
    [descriptor] = iter_consumer_leaves(schema)
    assert descriptor.path == ("x",) and descriptor.type == expected_type


def test_walker_excludes_path_parameter_under_normalized_name(ctx, case_factory, response_factory):
    schema, extra_data_source = _extra_data_source_for(
        ctx,
        {
            "/api/items/{userId}": {
                "get": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            # Response echoes the path id under a different casing.
                                            "user_id": {"type": "string"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    )
    operation = schema["/api/items/{userId}"]["GET"]
    case = case_factory(operation=operation, path_parameters={"userId": "abc"})
    response = transport.Response.from_any(
        response_factory.requests(
            content=b'{"user_id": "abc", "name": "Alice"}',
            content_type="application/json",
            status_code=200,
        )
    )
    extra_data_source.record_response(operation=operation, response=response, case=case)
    by_name = extra_data_source.semantic_index.by_name
    assert ("string", "userid") not in by_name, "echoed path identity leaked into the named pool"
    assert ("string", "name") in by_name


def test_overlay_validates_against_container_when_substitution_violates_parent_constraint():
    # Container-level `not` rejects a value the leaf schema would accept; substitution must revert.
    blocked = "blocked@example.com"
    container_schema = {
        "type": "object",
        "properties": {"email": {"type": "string", "format": "email"}},
        "required": ["email"],
        "not": {"properties": {"email": {"const": blocked}}, "required": ["email"]},
    }
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value=blocked,
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email"},
    )
    inner = st.fixed_dictionaries({"email": st.just("ok@example.com")})
    overlay = build_semantic_overlay(
        inner, [descriptor], index, jsonschema_rs.Draft202012Validator, container_schema=container_schema
    )

    drawn: list[dict] = []

    @given(overlay)
    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def collect(value):
        drawn.append(value)

    collect()
    assert drawn
    for body in drawn:
        assert body.get("email") != blocked, (
            f"container validator should reject substitution that violates parent `not`; saw {body}"
        )


def test_consumer_walker_drops_plain_header_internal_format():
    # Plain string headers/cookies must not receive pool substitutions that fail wire encoding.
    schema = {
        "type": "object",
        "properties": {"X-Tag": {"type": "string", "format": "_header_value"}},
    }
    assert iter_consumer_leaves(schema) == []


def test_consumer_walker_drops_known_header_internal_format():
    schema = {
        "type": "object",
        "properties": {"If-Match": {"type": "string", "format": "_if_match_header"}},
    }
    assert iter_consumer_leaves(schema) == []


def test_record_response_swallows_schema_resolution_failures(ctx, case_factory, response_factory):
    # An unresolvable response-schema $ref must not fail recording when only non-schema checks are enabled.
    schema = ctx.openapi.load_schema(
        {
            "/api/produce": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MissingType"}}},
                        }
                    }
                }
            }
        }
    )
    operation = schema["/api/produce"]["GET"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    case = case_factory(operation=operation)
    response = transport.Response.from_any(
        response_factory.requests(
            content=b'{"contact": "alice@example.com"}',
            content_type="application/json",
            status_code=200,
        )
    )
    extra_data_source.record_response(operation=operation, response=response, case=case)


def test_overlay_does_not_mutate_shared_example_dict():
    shared = {"email": "ok@example.com"}
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="harvested@example.com",
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email"},
    )
    inner = st.just(shared)
    overlay = build_semantic_overlay(inner, [descriptor], index, jsonschema_rs.Draft202012Validator)

    @given(overlay)
    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def collect(value):
        pass

    collect()
    assert shared == {"email": "ok@example.com"}, f"overlay mutated shared example dict in place; saw {shared}"


def _raise_on_call(_response, _context):
    raise RuntimeError("deserializer must not run for non-2xx semantic-only responses")


def test_record_response_skips_deserialization_for_non_2xx_on_semantic_only_op(ctx, case_factory, response_factory):
    # Non-2xx responses on semantic-only operations must not run the body deserializer.
    media_type = "application/x-pool-probe"
    register_deserializer(_raise_on_call, media_type)
    schema = ctx.openapi.load_schema(_PRODUCER_SCHEMA)
    operation = schema["/api/produce"]["GET"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert not extra_data_source.repository.descriptors_for_operation(operation.label)
    case = case_factory(operation=operation)
    response = transport.Response.from_any(
        response_factory.requests(content=b"...", content_type=media_type, status_code=400)
    )
    extra_data_source.record_response(operation=operation, response=response, case=case)


def test_strategy_caching_remains_enabled_for_semantic_only_operations(ctx):
    # Same args must return the same strategy object so the body cache survives semantic-only sources.
    schema = ctx.openapi.load_schema(
        {
            "/api/produce": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"contact": {"type": "string", "format": "email"}},
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
                                        "properties": {"contact": {"type": "string", "format": "email"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        }
    )
    operation = schema["/api/produce"]["POST"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert not extra_data_source.repository.descriptors_for_operation(operation.label)
    body = operation.body[0]
    config = schema.config.generation_for(operation=operation, phase="fuzzing")

    first = body.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source)
    second = body.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source)
    assert first is second, "semantic-only data source should not disable the body-strategy cache"


def test_body_cache_distinguishes_semantic_source_from_no_source(ctx):
    # Cache must key on the semantic source so a no-source strategy isn't reused after one shows up.
    schema = ctx.openapi.load_schema(
        {
            "/api/produce": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"contact": {"type": "string", "format": "email"}},
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
                                        "properties": {"contact": {"type": "string", "format": "email"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        }
    )
    operation = schema["/api/produce"]["POST"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    body = operation.body[0]
    config = schema.config.generation_for(operation=operation, phase="fuzzing")

    no_source = body.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=None)
    with_source = body.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source)
    assert no_source is not with_source, "cache must differentiate strategies built with vs without a semantic source"


def test_parameter_set_cache_distinguishes_semantic_source_from_no_source(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/api/produce": {
                "get": {
                    "parameters": [{"name": "label", "in": "query", "required": False, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"label": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        }
    )
    operation = schema["/api/produce"]["GET"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    parameter_set = operation.query
    config = schema.config.generation_for(operation=operation, phase="fuzzing")

    no_source = parameter_set.get_strategy(operation, config, GenerationMode.POSITIVE, extra_data_source=None)
    with_source = parameter_set.get_strategy(
        operation, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source
    )
    assert no_source is not with_source, (
        "parameter-set cache must differentiate strategies built with vs without a semantic source"
    )


def test_parameter_set_strategy_does_not_leak_generated_value_through_filter(ctx):
    # `is_valid_query` / `is_valid_header` expect a dict; the overlay can wrap the body in
    # `GeneratedValue` on a substitution. Positive mode must unwrap before the location-specific
    # filter, or the filter crashes with `AttributeError: 'GeneratedValue' object has no attribute 'items'`.
    schema = ctx.openapi.load_schema(
        {
            "/api/items": {
                "get": {
                    "parameters": [{"name": "label", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"label": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        }
    )
    operation = schema["/api/items"]["GET"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert extra_data_source.semantic_index is not None
    extra_data_source.semantic_index.add(
        type_token="string",
        format_token=None,
        pattern_hash=None,
        normalized_name="label",
        value="harvested",
        source_operation="GET /producer",
    )
    parameter_set = operation.query
    config = schema.config.generation_for(operation=operation, phase="fuzzing")
    strategy = parameter_set.get_strategy(
        operation, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source
    )

    drawn: list[object] = []

    # 30 draws at 0.5 per-leaf substitution probability make it overwhelmingly likely the
    # overlay fires at least once; a single substitution exercises the filter path.
    @given(strategy)
    @settings(
        max_examples=30,
        derandomize=True,
        database=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def collect(value):
        drawn.append(value)

    collect()
    assert drawn, "strategy produced no values"


def test_strategy_caching_disabled_for_captured_variant_consumer(ctx):
    # Captured-variant consumers bind variants at build time, so the strategy must rebuild each call.
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}},
                                }
                            },
                        }
                    }
                }
            },
            "/users/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    consumer = schema["/users/{id}"]["GET"]
    extra_data_source = schema.analysis.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert consumer.label in extra_data_source.consumer_labels
    parameter_set = consumer.path_parameters
    config = schema.config.generation_for(operation=consumer, phase="fuzzing")

    first = parameter_set.get_strategy(consumer, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source)
    second = parameter_set.get_strategy(consumer, config, GenerationMode.POSITIVE, extra_data_source=extra_data_source)
    assert first is not second, (
        "consumer-side captured variants are bound at build time; the strategy must not be cached"
    )


def _email_provenance_setup():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="harvested@example.com",
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email"},
    )
    container_schema = {
        "type": "object",
        "properties": {"email": {"type": "string", "format": "email"}},
        "required": ["email"],
    }
    return index, [descriptor], container_schema


def test_overlay_records_semantic_draw_when_substitution_fires():
    index, descriptors, container = _email_provenance_setup()
    inner = st.fixed_dictionaries({"email": st.just("seed@seed.io")})
    overlay = build_semantic_overlay(
        inner, descriptors, index, jsonschema_rs.Draft202012Validator, container_schema=container
    )

    drawn: list[GeneratedValue] = []

    # 50 draws make at least one substitution near-certain at the 0.5 per-leaf probability.
    @given(overlay)
    @settings(max_examples=50, derandomize=True, database=None)
    def collect(value):
        drawn.append(value)

    collect()
    substituted = [v for v in drawn if isinstance(v, GeneratedValue) and v.semantic_draws]
    assert substituted, "expected at least one overlay substitution to be recorded"
    for value in substituted:
        assert value.semantic_draws == (
            SemanticDraw(
                path=("email",),
                type_token="string",
                format_token="email",
                pattern_hash=None,
                normalized_name="email",
                value="harvested@example.com",
                source_operation="GET /producer",
            ),
        )
        assert value.value == {"email": "harvested@example.com"}


def test_overlay_emits_no_semantic_draws_when_index_is_empty():
    _, descriptors, container = _email_provenance_setup()
    overlay = build_semantic_overlay(
        st.fixed_dictionaries({"email": st.just("seed@seed.io")}),
        descriptors,
        SemanticValueIndex(),
        jsonschema_rs.Draft202012Validator,
        container_schema=container,
    )

    drawn: list[object] = []

    @given(overlay)
    @settings(max_examples=30, derandomize=True, database=None)
    def collect(value):
        drawn.append(value)

    collect()
    for value in drawn:
        if isinstance(value, GeneratedValue):
            assert value.semantic_draws == ()
            assert value.value == {"email": "seed@seed.io"}
        else:
            assert value == {"email": "seed@seed.io"}


def test_add_is_noop_when_no_key_is_provided():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash=None,
        normalized_name=None,
        value="orphan",
        source_operation="GET /producer",
    )
    assert not index.by_format
    assert not index.by_pattern
    assert not index.by_name


def test_lookup_returns_empty_when_all_keys_are_none():
    index = SemanticValueIndex()
    assert index.lookup(type_token="string", format_token=None, pattern_hash=None, normalized_name=None) == ()


def test_lookup_falls_through_pattern_to_name_when_pattern_bucket_is_absent():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash=None,
        normalized_name="city",
        value="Berlin",
        source_operation="GET /producer",
    )
    assert index.lookup(type_token="string", format_token=None, pattern_hash="missing", normalized_name="city") == (
        SemanticCandidate("Berlin", "GET /producer"),
    )


def test_record_draw_uses_pattern_bucket_when_only_pattern_is_provided():
    phone_hash = pattern_hash("^\\+\\d+$")
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash=phone_hash,
        normalized_name=None,
        value="+12025551234",
        source_operation="GET /producer",
    )
    index.record_draw(
        type_token="string", format_token=None, pattern_hash=phone_hash, normalized_name=None, value="+12025551234"
    )
    assert index.lookup(type_token="string", format_token=None, pattern_hash=phone_hash, normalized_name=None) == (
        SemanticCandidate("+12025551234", "GET /producer"),
    )


def test_record_draw_uses_name_bucket_when_only_name_is_provided():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash=None,
        normalized_name="city",
        value="Berlin",
        source_operation="GET /producer",
    )
    index.record_draw(type_token="string", format_token=None, pattern_hash=None, normalized_name="city", value="Berlin")
    assert index.lookup(type_token="string", format_token=None, pattern_hash=None, normalized_name="city") == (
        SemanticCandidate("Berlin", "GET /producer"),
    )


def test_record_draw_is_noop_when_no_bucket_matches():
    SemanticValueIndex().record_draw(
        type_token="string", format_token=None, pattern_hash=None, normalized_name=None, value="anything"
    )


def test_walker_handles_ref_pointing_to_non_dict():
    schema = {
        "type": "object",
        "properties": {"email": {"$ref": "#/x-bundled/Bad"}},
        "x-bundled": {"Bad": 42},
    }
    assert list(iter_ingestion_leaves(schema, {"email": "a@b.com"})) == []


def test_walker_skips_non_dict_branch_in_allof():
    schema = {
        "allOf": [
            42,
            {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}},
        ]
    }
    assert list(iter_ingestion_leaves(schema, {"email": "a@b.com"})) == [
        IngestionLeaf("string", "email", None, "email", "a@b.com")
    ]


def test_walker_merges_outer_properties_with_allof_branches():
    schema = {
        "allOf": [{"type": "object", "properties": {"email": {"type": "string", "format": "email"}}}],
        "properties": {"name": {"type": "string"}},
    }
    body = {"email": "a@b.com", "name": "Alice"}
    assert {leaf.normalized_name for leaf in iter_ingestion_leaves(schema, body)} == {"email", "name"}


def test_walker_yields_nothing_when_allof_branches_lack_properties():
    schema = {"allOf": [{"type": "object"}, {"description": "wrapped"}]}
    assert list(iter_ingestion_leaves(schema, {"x": 1})) == []


def test_walker_skips_non_dict_branch_in_oneof():
    schema = {
        "oneOf": [
            True,
            {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}},
        ]
    }
    assert list(iter_ingestion_leaves(schema, {"email": "a@b.com"})) == [
        IngestionLeaf("string", "email", None, "email", "a@b.com")
    ]


def test_walker_falls_back_to_first_oneof_branch_when_none_declares_properties():
    schema = {"oneOf": [{"type": "string", "format": "email"}]}
    assert list(iter_ingestion_leaves(schema, "a@b.com")) == []


def test_walker_fallback_skips_non_dict_branches_in_oneof():
    # A oneOf list whose first entry is a non-dict must not stop the walk before reaching a usable branch.
    schema = {"oneOf": [42, {"type": "string"}]}
    assert list(iter_ingestion_leaves(schema, {"x": "y"})) == []


def test_walker_returns_original_when_oneof_has_no_dict_branches():
    schema = {"oneOf": [42, True]}
    assert list(iter_ingestion_leaves(schema, {"x": "y"})) == []


def test_walker_skips_property_whose_value_is_absent_from_body():
    schema = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "name": {"type": "string"},
        },
    }
    assert list(iter_ingestion_leaves(schema, {"name": "Alice"})) == [
        IngestionLeaf("string", None, None, "name", "Alice")
    ]


def test_walker_skips_object_schema_without_properties_keyword():
    schema = {"type": "object", "properties": {"nested": {"type": "object"}}}
    assert list(iter_ingestion_leaves(schema, {"nested": {"x": 1}})) == []


def test_walker_falls_back_to_schemaless_for_non_dict_property_schema():
    # When a property schema is a JSON boolean, leaves under it are still inferred from value shape.
    schema = {"type": "object", "properties": {"payload": True}}
    body = {"payload": {"contact": "alice@example.com"}}
    assert list(iter_ingestion_leaves(schema, body)) == [
        IngestionLeaf("string", "email", None, "contact", "alice@example.com")
    ]


def test_walker_handles_malformed_scalar_type_value():
    schema = {"type": "object", "properties": {"x": {"type": 42}}}
    assert list(iter_ingestion_leaves(schema, {"x": "value"})) == []


def test_walker_returns_no_leaf_when_type_list_has_only_null_and_non_string():
    schema = {"type": "object", "properties": {"x": {"type": [None, "null", 42]}}}
    assert list(iter_ingestion_leaves(schema, {"x": "value"})) == []


def test_walker_skips_boolean_primitive():
    # Booleans only have two values; Hypothesis enumerates them on its own, so pool reuse adds nothing.
    schema = {"type": "object", "properties": {"verified": {"type": "boolean"}}}
    assert list(iter_ingestion_leaves(schema, {"verified": True})) == []


def test_walker_clears_internal_format_marker_during_ingestion():
    schema = {"type": "object", "properties": {"tag": {"type": "string", "format": HEADER_FORMAT}}}
    assert list(iter_ingestion_leaves(schema, {"tag": "value"})) == [
        IngestionLeaf("string", None, None, "tag", "value")
    ]


def test_schemaless_walker_respects_max_depth():
    body = {"outer": {"inner": {"email": "a@b.com"}}}
    assert list(iter_ingestion_leaves(None, body, max_depth=1)) == []


def test_schemaless_walker_excludes_named_keys():
    body = {"id": "abc", "email": "alice@example.com"}
    assert list(iter_ingestion_leaves(None, body, excluded_names=frozenset({"id"}))) == [
        IngestionLeaf("string", "email", None, "email", "alice@example.com")
    ]


def test_schemaless_walker_respects_max_nodes():
    body = {f"k{i}": f"u{i}@example.com" for i in range(10)}
    leaves = list(iter_ingestion_leaves(None, body, max_nodes=5))
    assert 0 < len(leaves) < 10


def test_schemaless_walker_skips_top_level_array_body():
    assert list(iter_ingestion_leaves(None, [{"email": "a@b.com"}])) == []


def test_schemaless_walker_skips_nameless_scalar_body():
    assert list(iter_ingestion_leaves(None, "a@b.com")) == []


def test_consumer_walker_respects_max_depth():
    schema = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "email"}},
            }
        },
    }
    assert iter_consumer_leaves(schema, max_depth=0) == []


def test_consumer_walker_skips_non_dict_property_schema():
    schema = {
        "type": "object",
        "properties": {"x": True, "y": {"type": "string", "format": "email"}},
    }
    assert [descriptor.path for descriptor in iter_consumer_leaves(schema)] == [("y",)]


def test_consumer_walker_skips_object_schema_without_properties_keyword():
    assert iter_consumer_leaves({"type": "object"}) == []


def test_consumer_walker_skips_non_string_property_names():
    schema = {
        "type": "object",
        "properties": {
            42: {"type": "string", "format": "email"},
            "name": {"type": "string", "format": "email"},
        },
    }
    assert [descriptor.path for descriptor in iter_consumer_leaves(schema)] == [("name",)]


def test_consumer_walker_returns_empty_for_top_level_primitive_schema():
    assert iter_consumer_leaves({"type": "string", "format": "email"}) == []


def test_overlay_returns_base_unchanged_when_inner_yields_non_dict():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="x@y.com",
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email"},
    )
    overlay = build_semantic_overlay(st.just("plain string"), [descriptor], index, jsonschema_rs.Draft202012Validator)

    drawn: list[object] = []

    @given(overlay)
    @settings(max_examples=5, derandomize=True, database=None)
    def collect(value):
        drawn.append(value)

    collect()
    assert drawn and all(value == "plain string" for value in drawn)


def test_overlay_skips_substitution_when_descriptor_path_is_absent_from_body():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="x@y.com",
        source_operation="GET /producer",
    )
    descriptors = [
        LeafDescriptor(
            path=("missing_email",),
            type="string",
            format="email",
            pattern_hash=None,
            normalized_name="email",
            schema={"type": "string", "format": "email"},
        )
    ]
    overlay = build_semantic_overlay(
        st.just({"present_email": "seed@seed.io"}),
        descriptors,
        index,
        jsonschema_rs.Draft202012Validator,
    )

    drawn: list[object] = []

    @given(overlay)
    @settings(max_examples=20, derandomize=True, database=None)
    def collect(value):
        drawn.append(value.value if isinstance(value, GeneratedValue) else value)

    collect()
    for body in drawn:
        assert set(body) == {"present_email"}


def test_overlay_unwraps_inner_generated_value_and_appends_semantic_draws():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="harvested@example.com",
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email"},
    )
    inner_value = GeneratedValue(value={"email": "seed@seed.io"}, meta=None, pool_draws=(), semantic_draws=())
    overlay = build_semantic_overlay(st.just(inner_value), [descriptor], index, jsonschema_rs.Draft202012Validator)

    drawn: list[GeneratedValue] = []

    # 50 draws make recording at least one substitution near-certain at 0.5 per-leaf probability.
    @given(overlay)
    @settings(max_examples=50, derandomize=True, database=None)
    def collect(value):
        drawn.append(value)

    collect()
    assert drawn and all(isinstance(value, GeneratedValue) for value in drawn)
    assert any(value.semantic_draws for value in drawn)


def test_overlay_records_multiple_substitutions_in_a_single_draw():
    index = SemanticValueIndex()
    for normalized in ("email", "contact"):
        index.add(
            type_token="string",
            format_token="email",
            pattern_hash=None,
            normalized_name=normalized,
            value=f"{normalized}@example.com",
            source_operation="GET /producer",
        )
    descriptors = [
        LeafDescriptor(
            path=("email",),
            type="string",
            format="email",
            pattern_hash=None,
            normalized_name="email",
            schema={"type": "string", "format": "email"},
        ),
        LeafDescriptor(
            path=("contact",),
            type="string",
            format="email",
            pattern_hash=None,
            normalized_name="contact",
            schema={"type": "string", "format": "email"},
        ),
    ]
    inner = st.just({"email": "seed@seed.io", "contact": "seed@seed.io"})
    overlay = build_semantic_overlay(inner, descriptors, index, jsonschema_rs.Draft202012Validator)

    multi_subs: list[GeneratedValue] = []

    # Two independent substitutions at p=0.5 each, so ~25% of draws hit both; 80 examples make
    # missing every multi-substitution draw vanishingly unlikely.
    @given(overlay)
    @settings(max_examples=80, derandomize=True, database=None)
    def collect(value):
        if isinstance(value, GeneratedValue) and len(value.semantic_draws) == 2:
            multi_subs.append(value)

    collect()
    assert multi_subs, "expected at least one draw with two simultaneous substitutions"


@pytest.mark.parametrize(
    ("target", "path"),
    [
        ({"a": 1}, ("missing",)),
        ({"a": [1, 2]}, ("a", "b")),
        ({"a": {"b": 1}}, ("a", "missing")),
        ({}, ("a", "b")),
    ],
    ids=["missing-top-level", "non-dict-intermediate", "missing-last-segment", "empty-target"],
)
def test_get_at_path_returns_missing_for_absent_paths(target, path):
    assert _get_at_path(target, path) is _MISSING


def test_get_at_path_returns_value_for_present_path():
    assert _get_at_path({"a": {"b": 42}}, ("a", "b")) == 42


@pytest.mark.parametrize(
    ("target", "path"),
    [
        ({"a": 1}, ()),
        ({"a": {"b": 1}}, ("missing", "x")),
        ({"a": [1, 2]}, ("a", "x")),
        ({"a": {}}, ("a", "missing")),
    ],
    ids=["empty-path", "missing-intermediate", "non-dict-intermediate", "missing-leaf"],
)
def test_set_at_path_returns_false_for_unreachable_paths(target, path):
    snapshot = json.loads(json.dumps(target))
    assert _set_at_path(target, path, "new") is False
    assert target == snapshot


def test_set_at_path_writes_value_when_full_path_exists():
    target = {"a": {"b": 1}}
    assert _set_at_path(target, ("a", "b"), 99) is True
    assert target == {"a": {"b": 99}}


def test_overlay_continues_when_descriptor_schema_breaks_validator_construction():
    # A malformed regex must not stop substitution; the leaf gate is skipped for that descriptor.
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token=None,
        pattern_hash="hash-1",
        normalized_name="phone",
        value="+12025551234",
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("phone",),
        type="string",
        format=None,
        pattern_hash="hash-1",
        normalized_name="phone",
        schema={"type": "string", "pattern": "["},
    )
    overlay = build_semantic_overlay(
        st.just({"phone": "seed"}), [descriptor], index, jsonschema_rs.Draft202012Validator
    )

    drawn: list[object] = []

    @given(overlay)
    @settings(max_examples=30, derandomize=True, database=None)
    def collect(value):
        drawn.append(value)

    collect()
    assert any(isinstance(value, GeneratedValue) and value.value == {"phone": "+12025551234"} for value in drawn), (
        "overlay swallowed validator failure but stopped substituting"
    )


def test_overlay_continues_when_container_validator_construction_fails():
    index = SemanticValueIndex()
    index.add(
        type_token="string",
        format_token="email",
        pattern_hash=None,
        normalized_name="email",
        value="x@y.com",
        source_operation="GET /producer",
    )
    descriptor = LeafDescriptor(
        path=("email",),
        type="string",
        format="email",
        pattern_hash=None,
        normalized_name="email",
        schema={"type": "string", "format": "email"},
    )
    container_schema = {"type": "object", "properties": {"email": {"type": "string", "pattern": "["}}}
    overlay = build_semantic_overlay(
        st.just({"email": "seed@seed.io"}),
        [descriptor],
        index,
        jsonschema_rs.Draft202012Validator,
        container_schema=container_schema,
    )

    drawn: list[object] = []

    @given(overlay)
    @settings(max_examples=20, derandomize=True, database=None)
    def collect(value):
        drawn.append(value)

    collect()
    assert any(isinstance(value, GeneratedValue) and value.value == {"email": "x@y.com"} for value in drawn), (
        "container validator failure stopped overlay substitution"
    )
