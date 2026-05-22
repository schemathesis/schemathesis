import pathlib
import sys

import pytest

from schemathesis.core.jsonschema import bundle
from schemathesis.core.jsonschema.bundler import Bundler
from schemathesis.core.jsonschema.resolver import Resolver, make_root_resolver

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from tools.corpus.io import load_from_corpus, read_corpus_file  # noqa: E402

CORPUS_SWAGGER_20 = read_corpus_file("swagger-2.0")
COST_MANAGEMENT = load_from_corpus("azure.com/cost-management-costmanagement/2018-08-31.json", CORPUS_SWAGGER_20)
MANAGED_CLUSTERS = load_from_corpus("azure.com/containerservice-managedClusters/2020-01-01.json", CORPUS_SWAGGER_20)


@pytest.mark.benchmark(group="bundle-flat-references")
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

    resolver = make_root_resolver({"definitions": definitions})

    benchmark(bundle, schema, resolver, inline_recursive=True)


@pytest.mark.benchmark(group="bundle-deep-nested-references")
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

    resolver = make_root_resolver({"definitions": definitions})

    benchmark(bundle, schema, resolver, inline_recursive=True)


@pytest.mark.benchmark(group="bundle-duplicate-references")
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

    resolver = make_root_resolver({"definitions": definitions})

    benchmark(bundle, schema, resolver, inline_recursive=True)


def _bundle_many(schemas: list, resolver: Resolver) -> None:
    # Share a single Bundler across sites — matches production OpenApiSchema._bundler lifetime, exercises the cache.
    bundler = Bundler()
    for schema in schemas:
        bundler.bundle_for_generation(schema, resolver)


@pytest.mark.benchmark(group="bundle-recursive-self-ref")
def test_bundle_recursive_self_ref(benchmark):
    definitions = {
        "Node": {
            "type": "object",
            "required": ["id", "kind"],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "kind": {"type": "string", "enum": ["leaf", "branch", "root"]},
                "label": {"type": "string", "maxLength": 256},
                "value": {"type": "number"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        "createdAt": {"type": "string", "format": "date-time"},
                        "updatedAt": {"type": "string", "format": "date-time"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "children": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/Node"},
                },
                "parent": {"$ref": "#/definitions/Node"},
                "sibling": {"$ref": "#/definitions/Node"},
            },
        }
    }
    schemas = [{"$ref": "#/definitions/Node"} for _ in range(100)]

    resolver = make_root_resolver({"definitions": definitions})

    benchmark(_bundle_many, schemas, resolver)


@pytest.mark.benchmark(group="bundle-recursive-with-siblings")
def test_bundle_recursive_with_siblings(benchmark):
    definitions = {
        "ReportConfigComparisonExpression": {
            "type": "object",
            "required": ["name", "operator", "values"],
            "properties": {
                "name": {"type": "string"},
                "operator": {"type": "string", "enum": ["In", "Contains"]},
                "values": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
        },
        "ReportConfigFilter": {
            "type": "object",
            "properties": {
                "and": {
                    "type": "array",
                    "minItems": 2,
                    "items": {"$ref": "#/definitions/ReportConfigFilter"},
                },
                "or": {
                    "type": "array",
                    "minItems": 2,
                    "items": {"$ref": "#/definitions/ReportConfigFilter"},
                },
                "not": {"$ref": "#/definitions/ReportConfigFilter"},
                "dimension": {"$ref": "#/definitions/ReportConfigComparisonExpression"},
                "tag": {"$ref": "#/definitions/ReportConfigComparisonExpression"},
            },
        },
    }
    schemas = [{"$ref": "#/definitions/ReportConfigFilter"} for _ in range(250)]

    resolver = make_root_resolver({"definitions": definitions})

    benchmark(_bundle_many, schemas, resolver)


@pytest.mark.benchmark(group="bundle-mutual-recursion")
def test_bundle_mutual_recursion(benchmark):
    definitions = {
        "Shared": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        },
        "A": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "shared": {"$ref": "#/definitions/Shared"},
                "b": {"$ref": "#/definitions/B"},
                "bs": {"type": "array", "items": {"$ref": "#/definitions/B"}},
            },
        },
        "B": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "shared": {"$ref": "#/definitions/Shared"},
                "c": {"$ref": "#/definitions/C"},
                "cs": {"type": "array", "items": {"$ref": "#/definitions/C"}},
            },
        },
        "C": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "shared": {"$ref": "#/definitions/Shared"},
                "a": {"$ref": "#/definitions/A"},
                "as": {"type": "array", "items": {"$ref": "#/definitions/A"}},
            },
        },
    }
    # Cycle through entry points so the bundler walks each cycle direction.
    entries = ["A", "B", "C"]
    schemas = [{"$ref": f"#/definitions/{entries[i % 3]}"} for i in range(120)]

    resolver = make_root_resolver({"definitions": definitions})

    benchmark(_bundle_many, schemas, resolver)


def _collect_parameter_schemas(raw_schema: dict) -> list[dict]:
    schemas: list[dict] = []
    for path_item in raw_schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.startswith("x-") or not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []) or []:
                schema = parameter.get("schema") if isinstance(parameter, dict) else None
                if isinstance(schema, dict):
                    schemas.append(schema)
            for response in (operation.get("responses") or {}).values():
                schema = response.get("schema") if isinstance(response, dict) else None
                if isinstance(schema, dict):
                    schemas.append(schema)
    return schemas


def _bundle_each(schemas: list[dict], resolver: Resolver) -> None:
    bundler = Bundler()
    for schema in schemas:
        bundler.bundle_for_generation(schema, resolver)


@pytest.mark.benchmark(group="bundle-real-world")
@pytest.mark.parametrize(
    "raw_schema",
    [COST_MANAGEMENT, MANAGED_CLUSTERS],
    ids=("azure-cost-management", "azure-managed-clusters"),
)
def test_bundle_real_world(benchmark, raw_schema):
    resolver = make_root_resolver(raw_schema)
    # Duplicate the collected list to lift each iteration above the ~1 ms floor at
    # which CodSpeed starts producing usable flame graphs.
    schemas = _collect_parameter_schemas(raw_schema) * 8
    benchmark(_bundle_each, schemas, resolver)
