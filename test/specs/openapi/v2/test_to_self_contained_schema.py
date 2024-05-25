from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Any

import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._jsonschema import TransformConfig, to_self_contained_jsonschema


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
                "A": {"type": "object", "properties": {"id": {"type": "string"}}},
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
        moved_schemas={},
        schemas_behind_references={},
        recursive_references={},
        transformed_references={},
    )


def build_resolver(spec):
    registry = Registry().with_resource("", Resource(contents=spec, specification=DRAFT4))
    return registry.resolver()


ITERATIONS = 2


@pytest.mark.schema("non_recursive_ref")
def test_non_recursive_ref(ctx: Context):
    for _ in range(ITERATIONS):
        visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
        assert visited == {"-definitions-A"}
    assert ctx.config.schemas_behind_references == {"#/definitions/A": {"-definitions-A"}}
    assert ctx.config.moved_schemas == {"-definitions-A": {"type": "object", "properties": {"id": {"type": "string"}}}}


@pytest.mark.schema("schema_transformation")
def test_schema_transformation(ctx: Context):
    for _ in range(ITERATIONS):
        visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
        assert visited == {"-definitions-A"}
    assert ctx.config.schemas_behind_references == {"#/definitions/A": {"-definitions-A"}}
    assert ctx.config.moved_schemas == {
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


@pytest.mark.schema("recursive_1_hop")
def test_recursive_1_hop(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for ref in combo:
                visited = to_self_contained_jsonschema({"$ref": ref}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B"},
        "#/definitions/B": {"-definitions-A", "-definitions-B"},
        # Added on the second iteration. Schemas already have their references replaced with "moved" ones.
        "#/x-moved-schemas/-definitions-A": {"-definitions-A", "-definitions-B"},
        "#/x-moved-schemas/-definitions-B": {"-definitions-A", "-definitions-B"},
    }
    assert ctx.config.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-B"}},
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
        },
    }


@pytest.mark.schema("recursive_1_hop_in_array")
def test_recursive_1_hop_in_array(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for ref in combo:
                visited = to_self_contained_jsonschema({"$ref": ref}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B"},
        "#/definitions/B": {"-definitions-A", "-definitions-B"},
        # Added on the second iteration. Schemas already have their references replaced with "moved" ones.
        "#/x-moved-schemas/-definitions-A": {"-definitions-A", "-definitions-B"},
        "#/x-moved-schemas/-definitions-B": {"-definitions-A", "-definitions-B"},
    }
    assert ctx.config.moved_schemas == {
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


@pytest.mark.schema("recursive_2_hops")
def test_recursive_2_hops(ctx: Context):
    references = ("#/definitions/A", "#/definitions/B", "#/definitions/C")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for ref in combo:
                visited = to_self_contained_jsonschema({"$ref": ref}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B", "-definitions-C"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.schemas_behind_references == {
        "#/definitions/A": {"-definitions-A", "-definitions-B", "-definitions-C"},
        "#/definitions/B": {"-definitions-A", "-definitions-B", "-definitions-C"},
        "#/definitions/C": {"-definitions-A", "-definitions-B", "-definitions-C"},
        # Added on the second iteration. Schemas already have their references replaced with "moved" ones.
        "#/x-moved-schemas/-definitions-A": {"-definitions-A", "-definitions-B", "-definitions-C"},
        "#/x-moved-schemas/-definitions-B": {"-definitions-A", "-definitions-B", "-definitions-C"},
        "#/x-moved-schemas/-definitions-C": {"-definitions-A", "-definitions-B", "-definitions-C"},
    }
    assert ctx.config.moved_schemas == {
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
            for ref in combo:
                visited = to_self_contained_jsonschema({"$ref": ref}, ctx.resolver, ctx.config)
                assert visited == expected_visits[ref], ref
            if idx != ITERATIONS - 1:
                ctx.reset()
    for ref in references:
        moved_ref = ref.replace("#/definitions/", "#/x-moved-schemas/-definitions-")
        print(moved_ref)
    assert ctx.config.schemas_behind_references == {
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
        "#/x-moved-schemas/-definitions-RecursiveA": {"-definitions-RecursiveA", "-definitions-RecursiveB"},
        "#/x-moved-schemas/-definitions-RecursiveB": {"-definitions-RecursiveA", "-definitions-RecursiveB"},
        "#/x-moved-schemas/-definitions-RecursiveRoot": {
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
            "-definitions-RecursiveRoot",
        },
        "#/x-moved-schemas/-definitions-Shared": {
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
            "-definitions-RecursiveRoot",
            "-definitions-Shared",
        },
    }
    assert ctx.config.moved_schemas == {
        "-definitions-Patch": {"$ref": "#/x-moved-schemas/-definitions-Shared"},
        "-definitions-Put": {"$ref": "#/x-moved-schemas/-definitions-Shared"},
        "-definitions-RecursiveA": {"$ref": "#/x-moved-schemas/-definitions-RecursiveB"},
        "-definitions-RecursiveB": {"$ref": "#/x-moved-schemas/-definitions-RecursiveA"},
        "-definitions-RecursiveRoot": {"$ref": "#/x-moved-schemas/-definitions-RecursiveA"},
        "-definitions-Shared": {"$ref": "#/x-moved-schemas/-definitions-RecursiveRoot"},
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
            for ref in combo:
                visited = to_self_contained_jsonschema({"$ref": ref}, ctx.resolver, ctx.config)
                assert visited == expected_visits[ref]
            if idx != ITERATIONS - 1:
                ctx.reset()
    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Patch"}, ctx.resolver, ctx.config)
    assert ctx.config.schemas_behind_references == {
        **expected_visits,
        # Added on the second iteration. Schemas already have their references replaced with "moved" ones.
        # Except for `Patch` as it is not referenced anywhere
        "#/x-moved-schemas/-definitions-Shared": {"-definitions-Leaf", "-definitions-Put", "-definitions-Shared"},
        "#/x-moved-schemas/-definitions-Put": {"-definitions-Leaf", "-definitions-Put", "-definitions-Shared"},
        "#/x-moved-schemas/-definitions-Leaf": {"-definitions-Leaf"},
    }
    # assert config.recursive_references == {
    #     # From `Shared` one can find 2 recursive references, `Shared` itself and `Put`
    #     "-definitions-Shared": {"-definitions-Shared", "-definitions-Put"},
    #     # And vice versa
    #     "-definitions-Put": {"-definitions-Shared", "-definitions-Put"},
    # }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Put"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"}
    assert ctx.config.schemas_behind_references == {
        "#/definitions/Shared": {"-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Put` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        "#/definitions/Put": {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"},
        # `Leaf` has no children
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    # assert config.recursive_references == {
    #     "-definitions-Shared": {"-definitions-Shared", "-definitions-Put"},
    #     "-definitions-Put": {"-definitions-Put"},
    # }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Shared"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"}
    assert ctx.config.schemas_behind_references == {
        "#/definitions/Shared": {"-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Put` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        "#/definitions/Put": {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"},
        # `Leaf` has no children
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    # assert config.recursive_references == {
    #     "-definitions-Shared": {"-definitions-Shared", "-definitions-Put"},
    #     "-definitions-Put": {"-definitions-Put"},
    # }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Leaf"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Leaf"}
    assert ctx.config.schemas_behind_references == {
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    # assert config.recursive_references == {
    # }
