"""Benchmarks for the coverage phase."""

import pytest
from jsonschema import Draft202012Validator

from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.coverage import CoverageContext, cover_schema_iter
from schemathesis.generation.hypothesis import setup

setup()

CONTEXTS = [
    CoverageContext(
        root_schema={},
        location=ParameterLocation.BODY,
        media_type=("application", "json"),
        is_required=True,
        custom_formats={},
        validator_cls=Draft202012Validator,
    ).with_positive(),
    CoverageContext(
        root_schema={},
        location=ParameterLocation.BODY,
        media_type=("application", "json"),
        is_required=True,
        custom_formats={},
        validator_cls=Draft202012Validator,
    ).with_negative(),
]
CONTEXT_NAMES = [",".join([m.value for m in ctx.generation_modes]) for ctx in CONTEXTS]

BASIC_TYPES = [
    {"type": "string"},
    {"type": "integer"},
    {"type": "number"},
    {"type": "boolean"},
    {"type": "null"},
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", BASIC_TYPES, ids=lambda x: f"basic-{x['type']}")
def test_basic_types(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))


STRING_CONSTRAINTS = [
    {"type": "string", "minLength": 5},
    {"type": "string", "maxLength": 10},
    {"type": "string", "pattern": "^[a-z]+$"},
    {"type": "string", "format": "email"},
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", STRING_CONSTRAINTS, ids=lambda x: f"string-{list(x.keys())[1]}")
def test_string_constraints(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))


NUMBER_CONSTRAINTS = [
    {"type": "number", "minimum": 0, "maximum": 100},
    {"type": "integer", "multipleOf": 5},
    {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", NUMBER_CONSTRAINTS, ids=lambda x: f"number-{list(x.keys())[1]}")
def test_number_constraints(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))


ARRAY_CONSTRAINTS = [
    {"type": "array", "items": {"type": "string"}},
    {"type": "array", "minItems": 2, "maxItems": 5},
    {"type": "array", "uniqueItems": True},
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", ARRAY_CONSTRAINTS, ids=lambda x: f"array-{list(x.keys())[1]}")
def test_array_constraints(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))


OBJECT_CONSTRAINTS = [
    {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}},
    {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
    {"type": "object", "additionalProperties": False, "properties": {"key": {"type": "string"}}},
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", OBJECT_CONSTRAINTS, ids=lambda x: f"object-{list(x.keys())[1]}")
def test_object_constraints(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))


# Combined schemas
COMBINED_SCHEMAS = [
    {"anyOf": [{"type": "string"}, {"type": "integer"}]},
    {"oneOf": [{"type": "number", "multipleOf": 5}, {"type": "number", "multipleOf": 3}]},
    {"allOf": [{"type": "string"}, {"minLength": 5}]},
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", COMBINED_SCHEMAS, ids=lambda x: f"combined-{list(x.keys())[0]}")
def test_combined_schemas(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))


COMPLEX_NESTED_SCHEMAS = [
    {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer", "minimum": 18},
                    "emails": {"type": "array", "items": {"type": "string", "format": "email"}},
                },
                "required": ["name", "age"],
            }
        },
    },
    {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            "required": ["id"],
        },
        "minItems": 1,
        "maxItems": 10,
    },
]


@pytest.mark.benchmark
@pytest.mark.parametrize("ctx", CONTEXTS, ids=CONTEXT_NAMES)
@pytest.mark.parametrize("schema", COMPLEX_NESTED_SCHEMAS, ids=lambda x: f"complex-{x['type']}")
def test_complex_nested_schemas(benchmark, ctx, schema):
    benchmark(lambda: list(cover_schema_iter(ctx, schema)))
