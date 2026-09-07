import pytest

from schemathesis.config import InferenceAlgorithm
from schemathesis.core.jsonschema.resolver import make_root_resolver
from schemathesis.core.transport import Response
from schemathesis.resources import Cardinality
from schemathesis.specs.openapi.runtime_inference import (
    OBSERVED_BODY_CAPACITY,
    ObservedBodyStore,
    _is_info_poor,
    synthesize_schema,
)
from schemathesis.specs.openapi.stateful.dependencies import analyze


@pytest.fixture
def empty_resolver():
    return make_root_resolver({})


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "object", "title": "PagedResponse"}, True),
        ({"type": "object"}, True),
        ({}, True),
        ({"description": "anything"}, True),
        ({"title": "Foo"}, True),
        ({"type": "object", "properties": {"id": {"type": "string"}}}, False),
        ({"type": "object", "items": {"type": "string"}}, False),
        ({"type": "object", "additionalProperties": False}, False),
        ({"type": "object", "additionalProperties": {"type": "string"}}, False),
        ({"allOf": [{"type": "object"}]}, False),
        ({"oneOf": [{"type": "object"}]}, False),
        ({"anyOf": [{"type": "object"}]}, False),
        ({"type": "array"}, False),
        ({"type": "string"}, False),
        ({"enum": ["a", "b"]}, False),
        ({"const": "x"}, False),
        (True, False),
        (None, False),
    ],
    ids=[
        "object-with-title-only",
        "object-bare-type",
        "fully-empty",
        "description-only",
        "title-only",
        "with-properties",
        "with-items",
        "additionalProperties-false",
        "additionalProperties-schema",
        "allOf",
        "oneOf",
        "anyOf",
        "array",
        "string",
        "enum",
        "const",
        "boolean-true",
        "none",
    ],
)
def test_is_info_poor_inline(schema, expected, empty_resolver):
    assert _is_info_poor(schema, empty_resolver) is expected


def test_is_info_poor_ref_to_opaque_object(ctx):
    schema = ctx.openapi.load_schema(
        {"/x": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Paged"}}}}}}}},
        components={"schemas": {"Paged": {"type": "object", "title": "PagedResponse"}}},
    )
    resolver = schema.root_resolver
    response = schema["/x"]["GET"].responses.get("200")
    raw = response.definition.get("content", {}).get("application/json", {}).get("schema")
    assert _is_info_poor(raw, resolver) is True


def test_is_info_poor_ref_to_declared_object(ctx):
    schema = ctx.openapi.load_schema(
        {"/x": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}}}}}}},
        components={"schemas": {"Item": {"type": "object", "properties": {"id": {"type": "string"}}}}},
    )
    resolver = schema.root_resolver
    response = schema["/x"]["GET"].responses.get("200")
    raw = response.definition.get("content", {}).get("application/json", {}).get("schema")
    assert _is_info_poor(raw, resolver) is False


def test_is_info_poor_handles_cyclic_ref(ctx):
    # `Loop` refers to itself — malformed but must not raise RecursionError.
    schema = ctx.openapi.load_schema(
        {"/x": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Loop"}}}}}}}},
        components={"schemas": {"Loop": {"$ref": "#/components/schemas/Loop"}}},
    )
    resolver = schema.root_resolver
    response = schema["/x"]["GET"].responses.get("200")
    raw = response.definition.get("content", {}).get("application/json", {}).get("schema")
    assert _is_info_poor(raw, resolver) is False


def test_is_info_poor_chained_ref_uses_resolved_resolver(ctx):
    schema = ctx.openapi.load_schema(
        {"/x": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Outer"}}}}}}}},
        components={
            "schemas": {
                "Outer": {"$ref": "#/components/schemas/Inner"},
                "Inner": {"type": "object", "title": "PagedResponse"},
            }
        },
    )
    resolver = schema.root_resolver
    response = schema["/x"]["GET"].responses.get("200")
    raw = response.definition.get("content", {}).get("application/json", {}).get("schema")
    assert _is_info_poor(raw, resolver) is True


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ([], None),
        (
            [{"id": 1, "name": "x"}, {"id": 2, "name": "y"}],
            {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}},
        ),
        (
            [{"content": [{"id": 1}]}, {"content": [{"id": 2}]}],
            {
                "type": "object",
                "properties": {
                    "content": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
                },
            },
        ),
        (["a", "b", "c"], {"type": "string"}),
        (
            [{"id": 1}, {"id": "x"}],
            {"type": "object", "properties": {"id": {"type": "integer"}}},
        ),
        (
            [{"id": "x"}, {"id": 1}],
            {"type": "object", "properties": {"id": {"type": "integer"}}},
        ),
        ([[]], {"type": "array"}),
        ([None, None], None),
        ([{}], {"type": "object"}),
    ],
    ids=[
        "empty-samples",
        "object-with-props",
        "nested-array-of-objects",
        "scalar-only-strings",
        "mixed-types-alpha-min-wins",
        "mixed-types-alpha-min-wins-reversed",
        "empty-array-body",
        "null-only",
        "empty-object",
    ],
)
def test_synthesize_schema(samples, expected):
    assert synthesize_schema(samples) == expected


def test_observed_body_store_dedupes_by_shape():
    store = ObservedBodyStore()
    assert store.record(operation="POST /foo", status_code=201, body={"id": 1}) is True
    assert store.record(operation="POST /foo", status_code=201, body={"id": 2}) is False
    assert store.record(operation="POST /foo", status_code=201, body={"id": "x"}) is True
    assert len(store.samples(operation="POST /foo", status_code=201)) == 2


def test_observed_body_store_tracks_dirty_keys():
    store = ObservedBodyStore()
    store.record(operation="POST /foo", status_code=201, body={"id": 1})
    store.record(operation="GET /bar", status_code=200, body=[{"id": 2}])
    assert store.dirty() == {("POST /foo", 201), ("GET /bar", 200)}
    drained = store.consume_dirty()
    assert drained == {("POST /foo", 201), ("GET /bar", 200)}
    assert store.dirty() == set()


def test_observed_body_store_caps_per_key():
    store = ObservedBodyStore()
    for index in range(OBSERVED_BODY_CAPACITY + 5):
        body = {f"field_{index}": index}
        store.record(operation="POST /foo", status_code=201, body=body)
    assert len(store.samples(operation="POST /foo", status_code=201)) == OBSERVED_BODY_CAPACITY


def test_extra_data_source_records_observed_body(ctx, response_factory):

    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object", "title": "X"}}}}
                    }
                }
            },
            "/things/{thing_id}": {
                "get": {
                    "parameters": [
                        {"name": "thing_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.analysis.extra_data_source
    assert data_source is not None, "consumer op should have materialized the data source"
    operation = schema["/things"]["GET"]
    case = operation.Case()
    raw = response_factory.requests(status_code=200, content=b'{"content": [{"id": 1}]}')
    data_source.record_observed_body(
        operation=operation, response=Response.from_requests(raw, True), case=case
    )
    samples = data_source.observed_bodies.samples(operation=operation.label, status_code=200)
    assert samples == [{"content": [{"id": 1}]}]


def test_record_response_does_NOT_populate_observed_bodies(ctx, response_factory):
    # Guard against re-introducing the unit-phase leak: record_response stays out of the
    # observed-body store; only record_observed_body writes there.

    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object", "title": "X"}}}}
                    }
                }
            },
            "/things/{thing_id}": {
                "get": {
                    "parameters": [
                        {"name": "thing_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.analysis.extra_data_source
    operation = schema["/things"]["GET"]
    case = operation.Case()
    raw = response_factory.requests(status_code=200, content=b'{"content": [{"id": 1}]}')
    data_source.record_response(operation=operation, response=Response.from_requests(raw, True), case=case)
    assert data_source.observed_bodies.samples(operation=operation.label, status_code=200) == []


def test_analyze_overlay_replaces_info_poor_response_schema(ctx):

    schema = ctx.openapi.load_schema(
        {
            "/albums": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object", "title": "PagedResponse"}}}}
                    }
                }
            }
        }
    )
    overlay = {
        ("GET /albums", 200): {
            "type": "object",
            "properties": {
                "content": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                }
            },
        }
    }
    graph = analyze(schema, overlay=overlay)
    outputs = graph.operations["GET /albums"].outputs
    assert outputs, "expected at least one synthesized output"
    output = outputs[0]
    # Extractor reports the collection pointer + Cardinality.MANY; the wildcard `*/id`
    # is reconstructed downstream during link generation. What we verify here: with the
    # overlay applied, the info-poor declared schema no longer hides the inner resource.
    assert output.pointer == "/content"
    assert output.cardinality == Cardinality.MANY
    assert output.resource.fields == ["id"]


_CHECKPOINT_SCHEMA = {
    "/albums": {
        "get": {
            "responses": {
                "200": {"content": {"application/json": {"schema": {"type": "object", "title": "PagedResponse"}}}}
            }
        }
    },
    "/photos": {
        "post": {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"albumId": {"type": "string"}},
                            "required": ["albumId"],
                        }
                    }
                },
            },
            "responses": {"201": {"description": "OK"}},
        }
    },
}


def test_analysis_checkpoint_synthesizes_info_poor_response(ctx, response_factory):

    schema = ctx.openapi.load_schema(_CHECKPOINT_SCHEMA)
    data_source = schema.analysis.extra_data_source
    assert data_source is not None
    operation = schema["/albums"]["GET"]
    case = operation.Case()
    raw = response_factory.requests(status_code=200, content=b'{"content": [{"id": "album-a"}]}')
    data_source.record_observed_body(operation=operation, response=Response.from_requests(raw, True), case=case)

    changed = schema.analysis.checkpoint()
    assert changed is True

    # After checkpoint, the synthesized overlay drives descriptor extraction: the previously
    # info-poor producer surfaces `Album` with `id`, anchored at the `/content` collection
    # (Cardinality.MANY). The wildcard `*/id` is reconstructed downstream during link generation.
    descriptors = schema.analysis.resource_descriptors
    album_descriptors = [d for d in descriptors if d.operation == "GET /albums"]
    assert any(
        d.resource_name == "Album" and d.pointer == "/content" and d.cardinality.value == "MANY"
        for d in album_descriptors
    ), album_descriptors

    # Idempotent: second call without new shapes returns False.
    assert schema.analysis.checkpoint() is False


def test_analysis_checkpoint_respects_runtime_synthesis_opt_out(ctx, response_factory):

    schema = ctx.openapi.load_schema(_CHECKPOINT_SCHEMA)
    schema.config.phases.stateful.inference.algorithms = [InferenceAlgorithm.DEPENDENCY_ANALYSIS]
    data_source = schema.analysis.extra_data_source
    assert data_source is not None
    operation = schema["/albums"]["GET"]
    case = operation.Case()
    raw = response_factory.requests(status_code=200, content=b'{"content": [{"id": "album-a"}]}')
    data_source.record_observed_body(operation=operation, response=Response.from_requests(raw, True), case=case)

    assert schema.analysis.checkpoint() is False


def test_analysis_checkpoint_skips_empty_array_synth(ctx, response_factory):
    # Empty-array bodies synthesize to {type: array} with no items — no extraction signal.

    schema = ctx.openapi.load_schema(_CHECKPOINT_SCHEMA)
    data_source = schema.analysis.extra_data_source
    operation = schema["/albums"]["GET"]
    case = operation.Case()
    raw = response_factory.requests(status_code=200, content=b"[]")
    data_source.record_observed_body(operation=operation, response=Response.from_requests(raw, True), case=case)

    assert schema.analysis.checkpoint() is False
