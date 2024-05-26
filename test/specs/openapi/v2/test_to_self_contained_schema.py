from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Any

import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._jsonschema import TransformConfig, to_self_contained_jsonschema
from schemathesis.specs.openapi._jsonschema.cache import TransformCache


@dataclass
class Context:
    resolver: Any
    config: TransformConfig
    spec: dict[str, Any]

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> Context:
        original = fast_deepcopy(spec)
        resolver = build_resolver(spec)
        config = build_config(spec)
        return cls(resolver=resolver, config=config, spec=original)

    def reset(self):
        spec = fast_deepcopy(self.spec)
        self.resolver = build_resolver(spec)
        self.config = build_config(spec)


def pytest_configure(config):
    config.addinivalue_line("markers", "schema(name): Add only specified API operations to the test application.")


@pytest.fixture
def non_recursive_ref() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "A": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "foo": True,
                        "bar": {
                            "anyOf": [True, {"type": "integer"}],
                        },
                    },
                },
                "B": {"type": "array", "items": [True, {"type": "object"}]},
            }
        }
    )


@pytest.fixture
def non_recursive_with_multiple_refs() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "A": {
                    "type": "object",
                    "properties": {
                        "first": {"$ref": "#/definitions/B"},
                        "second": {"$ref": "#/definitions/B"},
                    },
                },
                "B": {"type": "string"},
            }
        }
    )


@pytest.fixture
def schema_transformation() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "A": {
                    "type": "object",
                    "properties": {
                        "first": {"type": "file"},
                        "second": {"type": "string", "x-nullable": True},
                        "third": {
                            "type": "integer",
                            "readOnly": True,
                        },
                        "nested": {
                            "allOf": [{"type": "file"}, {"type": "null"}],
                        },
                    },
                },
            }
        }
    )


@pytest.fixture
def recursive_1_hop() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "A": {"type": "object", "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/definitions/B"}}},
                "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/definitions/A"}}},
            }
        }
    )


@pytest.fixture
def recursive_1_hop_in_array() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "A": {
                    "allOf": [
                        {"type": "string"},
                        {"$ref": "#/definitions/B"},
                    ]
                },
                "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/definitions/A"}}},
            }
        }
    )


@pytest.fixture
def recursive_2_hops() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "A": {"type": "object", "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/definitions/B"}}},
                "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/definitions/C"}}},
                "C": {
                    "type": "object",
                    "properties": {"email": {"type": "string"}, "ref": {"$ref": "#/definitions/A"}},
                },
            }
        }
    )


@pytest.fixture
def recursive_with_nested() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "Patch": {"$ref": "#/definitions/Shared"},
                "Put": {"$ref": "#/definitions/Shared"},
                "Shared": {"$ref": "#/definitions/RecursiveRoot"},
                "RecursiveRoot": {"$ref": "#/definitions/RecursiveA"},
                "RecursiveA": {"$ref": "#/definitions/RecursiveB"},
                "RecursiveB": {"$ref": "#/definitions/RecursiveA"},
            }
        }
    )


@pytest.fixture
def recursive_with_leaf() -> Context:
    return Context.from_spec(
        {
            "definitions": {
                "Patch": {"$ref": "#/definitions/Shared"},
                "Put": {"$ref": "#/definitions/Shared"},
                "Shared": {
                    "properties": {
                        "left": {
                            "$ref": "#/definitions/Leaf",
                        },
                        "right": {
                            "$ref": "#/definitions/Put",
                        },
                    },
                },
                "Leaf": {},
            }
        }
    )


@pytest.fixture
def ctx(request) -> Context:
    marker = request.node.get_closest_marker("schema")
    return request.getfixturevalue(marker.args[0])


def build_config(spec):
    return TransformConfig(
        nullable_key="x-nullable",
        remove_write_only=False,
        remove_read_only=True,
        components=spec,
        cache=TransformCache(),
    )


def build_resolver(spec):
    registry = Registry().with_resource("", Resource(contents=spec, specification=DRAFT4))
    return registry.resolver()


ITERATIONS = 2


@pytest.mark.schema("non_recursive_ref")
def test_non_recursive_ref(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B")
    expected_visits = {
        "#/definitions/A": {"-definitions-A"},
        "#/definitions/B": {"-definitions-B"},
    }
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == expected_visits[reference]
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A"},
        "#/definitions/B": {"-definitions-B"},
    }
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "foo": True,
                "bar": {
                    "anyOf": [True, {"type": "integer"}],
                },
            },
        },
        "-definitions-B": {"type": "array", "items": [True, {"type": "object"}]},
    }
    assert ctx.config.cache.recursive_references == {}


@pytest.mark.schema("non_recursive_with_multiple_refs")
def test_non_recursive_with_multiple_refs(ctx: Context):
    for _ in range(ITERATIONS):
        visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
        assert visited == {"-definitions-A", "-definitions-B"}
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B"},
        "#/definitions/B": {"-definitions-B"},
    }
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {
                "first": {"$ref": "#/x-moved-schemas/-definitions-B"},
                "second": {"$ref": "#/x-moved-schemas/-definitions-B"},
            },
        },
        "-definitions-B": {"type": "string"},
    }
    assert ctx.config.cache.recursive_references == {}


@pytest.mark.schema("schema_transformation")
def test_schema_transformation(ctx: Context):
    for _ in range(ITERATIONS):
        visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
        assert visited == {"-definitions-A"}
    assert ctx.config.cache.schemas_behind_references == {"#/definitions/A": {"-definitions-A"}}
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "not": {"required": ["third"]},
            "properties": {
                "first": {"type": "string", "format": "binary"},
                "nested": {"allOf": [{"type": "string", "format": "binary"}, {"type": "null"}]},
                "second": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "type": "object",
        }
    }
    assert ctx.config.cache.recursive_references == {}


@pytest.mark.schema("recursive_1_hop")
def test_recursive_1_hop(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B"},
        "#/definitions/B": {"-definitions-A", "-definitions-B"},
    }
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-B"}},
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
        },
    }
    assert ctx.config.cache.recursive_references == {
        "-definitions-A": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
        },
        "-definitions-B": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
        },
    }


@pytest.mark.schema("recursive_1_hop_in_array")
def test_recursive_1_hop_in_array(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B"},
        "#/definitions/B": {"-definitions-A", "-definitions-B"},
    }
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "allOf": [
                {"type": "string"},
                {"$ref": "#/x-moved-schemas/-definitions-B"},
            ]
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
        },
    }
    assert ctx.config.cache.recursive_references == {
        "-definitions-A": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
        },
        "-definitions-B": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
        },
    }


@pytest.mark.schema("recursive_2_hops")
def test_recursive_2_hops(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B", "#/definitions/C")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B", "-definitions-C"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B", "-definitions-C"},
        "#/definitions/B": {"-definitions-A", "-definitions-B", "-definitions-C"},
        "#/definitions/C": {"-definitions-A", "-definitions-B", "-definitions-C"},
    }
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-B"}},
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-C"}},
        },
        "-definitions-C": {
            "properties": {"email": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
            "type": "object",
        },
    }
    assert ctx.config.cache.recursive_references == {
        "-definitions-A": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
            "#/x-moved-schemas/-definitions-C",
        },
        "-definitions-B": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
            "#/x-moved-schemas/-definitions-C",
        },
        "-definitions-C": {
            "#/x-moved-schemas/-definitions-A",
            "#/x-moved-schemas/-definitions-B",
            "#/x-moved-schemas/-definitions-C",
        },
    }


@pytest.mark.schema("recursive_with_nested")
def test_recursive_with_nested(ctx: Context):
    references = (
        "#/definitions/Patch",
        "#/definitions/Put",
        "#/definitions/Shared",
        "#/definitions/RecursiveRoot",
        "#/definitions/RecursiveA",
        "#/definitions/RecursiveB",
    )
    expected_visits = {
        "#/definitions/Patch": {
            "-definitions-Patch",
            "-definitions-Shared",
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/Put": {
            "-definitions-Put",
            "-definitions-Shared",
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/Shared": {
            "-definitions-Shared",
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/RecursiveRoot": {
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/RecursiveA": {
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/RecursiveB": {
            "-definitions-RecursiveB",
            "-definitions-RecursiveA",
        },
    }
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == expected_visits[reference], reference
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/Patch": {
            "-definitions-Patch",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
            "-definitions-RecursiveRoot",
            "-definitions-Shared",
        },
        "#/definitions/Put": {
            "-definitions-Put",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
            "-definitions-RecursiveRoot",
            "-definitions-Shared",
        },
        "#/definitions/RecursiveA": {"-definitions-RecursiveA", "-definitions-RecursiveB"},
        "#/definitions/RecursiveB": {"-definitions-RecursiveA", "-definitions-RecursiveB"},
        "#/definitions/RecursiveRoot": {
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
            "-definitions-RecursiveRoot",
        },
        "#/definitions/Shared": {
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
            "-definitions-RecursiveRoot",
            "-definitions-Shared",
        },
    }
    assert ctx.config.cache.moved_schemas == {
        "-definitions-Patch": {"$ref": "#/x-moved-schemas/-definitions-Shared"},
        "-definitions-Put": {"$ref": "#/x-moved-schemas/-definitions-Shared"},
        "-definitions-RecursiveA": {"$ref": "#/x-moved-schemas/-definitions-RecursiveB"},
        "-definitions-RecursiveB": {"$ref": "#/x-moved-schemas/-definitions-RecursiveA"},
        "-definitions-RecursiveRoot": {"$ref": "#/x-moved-schemas/-definitions-RecursiveA"},
        "-definitions-Shared": {"$ref": "#/x-moved-schemas/-definitions-RecursiveRoot"},
    }
    assert ctx.config.cache.recursive_references == {
        "-definitions-RecursiveA": {
            "#/x-moved-schemas/-definitions-RecursiveA",
            "#/x-moved-schemas/-definitions-RecursiveB",
        },
        "-definitions-RecursiveB": {
            "#/x-moved-schemas/-definitions-RecursiveA",
            "#/x-moved-schemas/-definitions-RecursiveB",
        },
    }


@pytest.mark.schema("recursive_with_leaf")
def test_recursive_with_leaf(ctx: Context):
    references = ("#/definitions/Patch", "#/definitions/Put", "#/definitions/Shared", "#/definitions/Leaf")
    expected_visits = {
        # `Patch` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        # `Put` -> `Shared`
        "#/definitions/Patch": {"-definitions-Patch", "-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Shared` -> `Leaf`, `Put`
        # `Put` -> `Shared`
        "#/definitions/Shared": {"-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Put` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        "#/definitions/Put": {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"},
        # `Leaf` has no children
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == expected_visits[reference]
            if idx != ITERATIONS - 1:
                ctx.reset()
    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Patch"}, ctx.resolver, ctx.config)
    assert ctx.config.cache.schemas_behind_references == expected_visits
    assert ctx.config.cache.recursive_references == {
        "-definitions-Shared": {
            "#/x-moved-schemas/-definitions-Shared",
            "#/x-moved-schemas/-definitions-Put",
        },
        "-definitions-Put": {
            "#/x-moved-schemas/-definitions-Shared",
            "#/x-moved-schemas/-definitions-Put",
        },
    }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Put"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"}
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/Shared": {"-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Put` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        "#/definitions/Put": {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"},
        # `Leaf` has no children
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    assert ctx.config.cache.recursive_references == {
        "-definitions-Shared": {
            "#/x-moved-schemas/-definitions-Shared",
            "#/x-moved-schemas/-definitions-Put",
        },
        "-definitions-Put": {
            "#/x-moved-schemas/-definitions-Shared",
            "#/x-moved-schemas/-definitions-Put",
        },
    }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Shared"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"}
    assert ctx.config.cache.schemas_behind_references == {
        "#/definitions/Shared": {"-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Put` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        "#/definitions/Put": {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"},
        # `Leaf` has no children
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    assert ctx.config.cache.recursive_references == {
        "-definitions-Shared": {
            "#/x-moved-schemas/-definitions-Put",
            "#/x-moved-schemas/-definitions-Shared",
        },
        "-definitions-Put": {
            "#/x-moved-schemas/-definitions-Put",
            "#/x-moved-schemas/-definitions-Shared",
        },
    }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Leaf"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Leaf"}
    assert ctx.config.cache.schemas_behind_references == {"#/definitions/Leaf": {"-definitions-Leaf"}}
    assert ctx.config.cache.recursive_references == {}
