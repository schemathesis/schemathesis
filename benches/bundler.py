import pytest

from schemathesis.core.compat import RefResolver
from schemathesis.core.jsonschema import bundle


@pytest.mark.benchmark
def test_bundle_many_flat_references(benchmark):
    definitions = {}
    for i in range(50):
        definitions[f"Type{i}"] = {
            "type": "object",
            "properties": {"id": {"type": "string"}, "value": {"type": "number"}},
        }

    schema = {
        "type": "object",
        "properties": {f"field{i}": {"$ref": f"#/definitions/Type{i}"} for i in range(50)},
    }

    resolver = RefResolver.from_schema({"definitions": definitions})

    benchmark(bundle, schema, resolver, inline_recursive=True)


@pytest.mark.benchmark
def test_bundle_deep_nested_references(benchmark):
    definitions = {}
    for i in range(20):
        if i == 19:
            definitions[f"Level{i}"] = {"type": "string"}
        else:
            definitions[f"Level{i}"] = {
                "type": "object",
                "properties": {"data": {"type": "string"}, "next": {"$ref": f"#/definitions/Level{i + 1}"}},
            }

    schema = {"$ref": "#/definitions/Level0"}

    resolver = RefResolver.from_schema({"definitions": definitions})

    benchmark(bundle, schema, resolver, inline_recursive=True)


@pytest.mark.benchmark
def test_bundle_duplicate_references(benchmark):
    definitions = {
        "User": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {
                    "type": "string",
                },
            },
        }
    }

    schema = {
        "type": "object",
        "properties": {f"user{i}": {"$ref": "#/definitions/User"} for i in range(100)},
        "items": [{"$ref": "#/definitions/User"} for _ in range(50)],
    }

    resolver = RefResolver.from_schema({"definitions": definitions})

    benchmark(bundle, schema, resolver, inline_recursive=True)
